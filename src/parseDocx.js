// 从 .docx（OOXML，实为 ZIP 包）提取纯文本，用于「项目策划 → 导入剧本」。
//
// 设计约束（IMA §08）：
// - REDUN：纯前端离线可用，不联网、不依赖 mammoth/docx 等重型库。
// - CLOSE-02：解析失败必须显式抛错并向上透传，禁止静默返回空文本冒充成功。
// 实现：用浏览器/Node 内置 DecompressionStream('deflate-raw') 解压 ZIP 条目，
// 再解析 word/document.xml 的 <w:t> 文本，按段落 <w:p> 换行、<w:br>/<w:tab> 转义。
// 兼容性增强（2026-08-08）：
// - 文件头 PK 校验，识别旧版 .doc（OLE）伪装的 .docx；
// - 支持通过 [Content_Types].xml 定位 document.xml（非标准路径/更名容错）；
// - deflate 解压失败时回退到 deflate（容错部分生成器带 zlib 包装）；
// - 文本解码健康检查：UTF-8 出现大量替换字符时尝试 GB18030；
// - 输出健康检查：乱码比例过高时显式抛错。

export async function parseDocx(arrayBuffer) {
  if (typeof DecompressionStream === 'undefined') {
    throw new Error('当前浏览器不支持 DecompressionStream，无法解析 .docx')
  }
  const buf = new Uint8Array(arrayBuffer)

  // 文件头校验：.docx 必须是 ZIP（PK\x03\x04 或 PK\x05\x06）
  const sig = buf.length >= 4 ? dvGetUint32(buf, 0) : 0
  if (sig !== 0x04034b50 && sig !== 0x06054b50) {
    // OLE2/.doc 文件头 0xD0CF11E0
    if (sig === 0xe011cfd0) {
      throw new Error('检测到旧版 Word .doc（OLE）格式，请使用 .docx 文件后再试')
    }
    throw new Error('不是有效的 .docx 文件（缺少 ZIP 文件头）')
  }

  const entries = readZipEntries(buf)
  if (!entries.length) throw new Error('.docx 内未找到任何压缩条目')

  // 优先通过 [Content_Types].xml 定位主文档（容错非标准路径/更名）
  let docPath = 'word/document.xml'
  const ctEntry = entries.find((e) => e.name === '[Content_Types].xml')
  if (ctEntry) {
    try {
      const ctXml = await inflateEntry(ctEntry, buf)
      const m = ctXml.match(/PartName="([^"]+)"[^>]*ContentType="[^"]*wordprocessingml\.document\.main\+xml"/)
        || ctXml.match(/ContentType="[^"]*wordprocessingml\.document\.main\+xml"[^>]*PartName="([^"]+)"/)
      if (m && m[1]) docPath = m[1].replace(/^\//, '')
    } catch { /* 忽略 Content_Types 解析失败，回退默认值 */ }
  }

  const docEntry = entries.find((e) => e.name === docPath)
  const fallback = entries.find(
    (e) => e.name.endsWith('.xml') && /(^|\/)word\//.test(e.name) && e.name !== docPath
  )
  const target = docEntry || fallback
  if (!target) throw new Error('.docx 内未找到 ' + docPath + ' 或 word/ 下任何 XML')

  const xml = await inflateEntry(target, buf)
  if (!xml || !xml.trim()) throw new Error('.docx 解压后内容为空')

  const text = docxXmlToText(xml)
  if (!text.trim()) throw new Error('未从 .docx 中解析出任何文本')

  // 健康检查：若替换字符/控制字符占比过高，认为解码失败
  const badRatio = measureGarbage(text)
  if (badRatio > 0.15) {
    throw new Error('.docx 文本解码异常（乱码比例 ' + Math.round(badRatio * 100) + '%），文件可能损坏或非标准格式')
  }
  return text
}

// 读取 ZIP 中央目录，返回条目元信息（名称 / 压缩方式 / 压缩大小 / 本地头偏移）
function readZipEntries(buf) {
  const dv = new DataView(buf.buffer, buf.byteOffset, buf.byteLength)
  let eocd = -1
  // EOCD 签名 0x06054b50，从尾部向前搜索（容错：注释区不定长）
  for (let i = buf.length - 22; i >= 0; i--) {
    if (dv.getUint32(i, true) === 0x06054b50) { eocd = i; break }
  }
  if (eocd < 0) throw new Error('不是有效的 .docx（找不到 ZIP 结尾目录）')

  const cdCount = dv.getUint16(eocd + 10, true)
  const cdOffset = dv.getUint32(eocd + 16, true)
  const entries = []
  let p = cdOffset
  for (let n = 0; n < cdCount; n++) {
    if (dv.getUint32(p, true) !== 0x02014b50) break // 中央目录文件头签名
    const method = dv.getUint16(p + 10, true)
    const compSize = dv.getUint32(p + 20, true)
    const nameLen = dv.getUint16(p + 28, true)
    const extraLen = dv.getUint16(p + 30, true)
    const commentLen = dv.getUint16(p + 32, true)
    const localOffset = dv.getUint32(p + 42, true)
    let name = ''
    for (let k = 0; k < nameLen; k++) name += String.fromCharCode(buf[p + 46 + k])
    entries.push({ name, method, compSize, localOffset })
    p += 46 + nameLen + extraLen + commentLen
  }
  return entries
}

async function inflateEntry(entry, buf) {
  const dv = new DataView(buf.buffer, buf.byteOffset, buf.byteLength)
  const p = entry.localOffset
  if (dv.getUint32(p, true) !== 0x04034b50) throw new Error('ZIP 本地文件头损坏')
  const nameLen = dv.getUint16(p + 26, true)
  const extraLen = dv.getUint16(p + 28, true)
  const dataStart = p + 30 + nameLen + extraLen
  const compData = buf.subarray(dataStart, dataStart + entry.compSize)

  let bytes
  if (entry.method === 0) {
    bytes = compData // 存储（未压缩）
  } else if (entry.method === 8) {
    bytes = await inflateRaw(compData) // deflate
  } else {
    throw new Error('不支持的 ZIP 压缩方式: ' + entry.method)
  }
  return decodeText(bytes)
}

async function inflateRaw(data) {
  // 优先 raw deflate（标准 ZIP），失败时回退 zlib wrapped deflate（容错）
  for (const type of ['deflate-raw', 'deflate']) {
    try {
      const ds = new DecompressionStream(type)
      const writer = ds.writable.getWriter()
      writer.write(data)
      writer.close()
      const ab = await new Response(ds.readable).arrayBuffer()
      return new Uint8Array(ab)
    } catch (e) {
      if (type === 'deflate-raw') continue
      throw e
    }
  }
}

function decodeText(bytes) {
  // 优先 UTF-8；若替换字符比例高，则尝试 GB18030（兼容中文 Windows 生成器）
  const utf8 = new TextDecoder('utf-8', { fatal: false }).decode(bytes)
  if (measureGarbage(utf8) < 0.05) return utf8
  try {
    const gb = new TextDecoder('gb18030', { fatal: false }).decode(bytes)
    if (measureGarbage(gb) < measureGarbage(utf8)) return gb
  } catch { /* GB18030 不支持时忽略 */ }
  return utf8
}

function dvGetUint32(buf, offset) {
  return new DataView(buf.buffer, buf.byteOffset, buf.byteLength).getUint32(offset, true)
}

function measureGarbage(text) {
  // 统计 U+FFFD（替换字符）和异常控制字符（C0 控制字符，不含换行制表）比例
  let bad = 0
  for (let i = 0; i < text.length; i++) {
    const c = text.charCodeAt(i)
    if (c === 0xfffd) { bad++; continue }
    if (c < 0x20 && c !== 0x09 && c !== 0x0a && c !== 0x0d) bad++
  }
  return text.length ? bad / text.length : 0
}

function docxXmlToText(xml) {
  let t = xml
  // 段落 → 换行；换行/分页符 → 换行；制表符 → \t
  t = t.replace(/<\/w:p>/gi, '\n')
  t = t.replace(/<w:br\s*\/?>/gi, '\n')
  t = t.replace(/<w:tab\s*\/?>/gi, '\t')
  // 移除其余所有标签（保留 <w:t> 内文本）
  t = t.replace(/<[^>]+>/g, '')
  // 解码 XML 实体
  t = t
    .replace(/&amp;/g, '&')
    .replace(/&lt;/g, '<')
    .replace(/&gt;/g, '>')
    .replace(/&quot;/g, '"')
    .replace(/&apos;/g, "'")
  // 折叠多余空白：行尾空格、连续空行
  t = t.replace(/[ \t]+\n/g, '\n').replace(/\n{3,}/g, '\n\n').trim()
  return t
}

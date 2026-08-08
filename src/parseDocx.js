// 从 .docx（OOXML，实为 ZIP 包）提取纯文本，用于「项目策划 → 导入剧本」。
//
// 设计约束（IMA §08）：
// - REDUN：纯前端离线可用，不联网、不依赖 mammoth/docx 等重型库。
// - CLOSE-02：解析失败必须显式抛错并向上透传，禁止静默返回空文本冒充成功。
// 实现：用浏览器/Node 内置 DecompressionStream('deflate-raw') 解压 ZIP 条目，
// 再解析 word/document.xml 的 <w:t> 文本，按段落 <w:p> 换行、<w:br>/<w:tab> 转义。

export async function parseDocx(arrayBuffer) {
  if (typeof DecompressionStream === 'undefined') {
    throw new Error('当前浏览器不支持 DecompressionStream，无法解析 .docx')
  }
  const buf = new Uint8Array(arrayBuffer)
  const entries = readZipEntries(buf)
  if (!entries.length) throw new Error('.docx 内未找到任何压缩条目')

  // 优先正文 document.xml，其次 word/ 下任一 xml（兼容极少数缺 document.xml 的情况）
  const docEntry = entries.find((e) => e.name === 'word/document.xml')
  const fallback = entries.find(
    (e) => e.name.endsWith('.xml') && /(^|\/)word\//.test(e.name) && e.name !== 'word/document.xml'
  )
  const target = docEntry || fallback
  if (!target) throw new Error('.docx 内未找到 word/document.xml')

  const xml = await inflateEntry(target, buf)
  if (!xml) throw new Error('.docx 解压后内容为空')
  return docxXmlToText(xml)
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
  return new TextDecoder('utf-8').decode(bytes)
}

async function inflateRaw(data) {
  const ds = new DecompressionStream('deflate-raw')
  const writer = ds.writable.getWriter()
  writer.write(data)
  writer.close()
  const ab = await new Response(ds.readable).arrayBuffer()
  return new Uint8Array(ab)
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

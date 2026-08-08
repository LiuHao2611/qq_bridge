import { parse, compileScript, compileTemplate } from 'vue/compiler-sfc'
import { readFileSync } from 'fs'
const files = ['src/components/Creative.vue','src/components/Help.vue','src/components/Settings.vue']
let ok = true
for (const f of files) {
  try {
    const src = readFileSync(f, 'utf8')
    const { descriptor, errors } = parse(src, { filename: f })
    if (errors.length) { console.log('PARSE ERR', f, errors); ok = false; continue }
    const s = compileScript(descriptor, { id: f })
    if (descriptor.template) {
      const t = compileTemplate({ source: descriptor.template.content, filename: f, id: f, compilerOptions: { bindingMetadata: s.bindings } })
      if (t.errors.length) { console.log('TEMPLATE ERR', f, t.errors); ok = false }
    }
    console.log('OK', f, '(' + src.split('\n').length + ' lines)')
  } catch (e) { console.log('FAIL', f, e.message); ok = false }
}
console.log(ok ? 'ALL_OK' : 'HAS_ERROR')

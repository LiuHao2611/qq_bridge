import { ref } from 'vue'

// 模块级选中态（EditOverlay 与 PropertyPanel 共享）
const selectedPath = ref(null)

export function useEditor() {
  const select = (path) => { selectedPath.value = path }
  const deselect = () => { selectedPath.value = null }
  return { selectedPath, select, deselect }
}

// 按点路径读写属性（支持 style.fontSize / props.content 等）
export function getByPath(obj, key) {
  if (!obj || !key) return undefined
  return key.split('.').reduce((o, k) => (o == null ? undefined : o[k]), obj)
}
export function setByPath(obj, key, value) {
  if (!obj || !key) return
  const parts = key.split('.')
  let o = obj
  for (let i = 0; i < parts.length - 1; i++) {
    const k = parts[i]
    if (o[k] == null || typeof o[k] !== 'object') o[k] = {}
    o = o[k]
  }
  o[parts[parts.length - 1]] = value
}

// 按路径（children 索引链）取节点
export function getNodeByPath(tree, path) {
  if (!path) return tree
  let node = tree
  for (const seg of path.split('.')) {
    if (node && node.children && node.children[+seg] != null) node = node.children[+seg]
    else return null
  }
  return node
}

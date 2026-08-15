// 万能短剧 · 页面自定义 · 节点类型注册表
// 每个节点类型 → { component, schema }；schema 决定属性面板可编辑项 + 控件类型。
// 容器类（page/row/col/navbar/sidebar/card/dialog/subpage/space/divider）复用 ContainerNode。
import ContainerNode from '../renderer/nodes/ContainerNode.vue'
import TextNode from '../renderer/nodes/TextNode.vue'
import ButtonNode from '../renderer/nodes/ButtonNode.vue'
import InputNode from '../renderer/nodes/InputNode.vue'
import StatCardNode from '../renderer/nodes/StatCardNode.vue'
import TagNode from '../renderer/nodes/TagNode.vue'
import ProgressNode from '../renderer/nodes/ProgressNode.vue'
import ImgNode from '../renderer/nodes/ImgNode.vue'
import MenuItemNode from '../renderer/nodes/MenuItemNode.vue'

// 通用样式 schema（所有节点共有，追加到面板底部「样式」分组）
export const commonStyleSchema = [
  { key: 'style.background', label: '背景色', control: 'color' },
  { key: 'style.color', label: '前景色', control: 'color' },
  { key: 'style.border', label: '边框', control: 'text', placeholder: '2px solid #fff' },
  { key: 'style.borderRadius', label: '圆角', control: 'slider', min: 0, max: 24 },
  { key: 'style.boxShadow', label: '阴影', control: 'text', placeholder: '4px 4px 0 0 rgba(0,0,0,.9)' },
  { key: 'style.padding', label: '内边距', control: 'text', placeholder: '16px' },
  { key: 'style.margin', label: '外边距', control: 'text' },
  { key: 'style.opacity', label: '透明度', control: 'slider', min: 0, max: 1, step: 0.05 },
  { key: 'style.width', label: '宽度', control: 'text' },
  { key: 'style.height', label: '高度', control: 'text' },
  { key: 'style.fontFamily', label: '字体', control: 'select', options: ['var(--font-display)', 'var(--font-body)', 'var(--font-mono)', 'var(--font-num)'] },
  { key: 'style.transform', label: '变换 skew/rotate', control: 'text', placeholder: 'skewX(-6deg)' },
]

// 主题令牌快捷插入选项（属性面板「主题令牌」下拉）
export const themeTokens = [
  '--p5-red', '--p5-ink', '--p5-paper', '--p5-white', '--p5-gold',
  '--nc-yellow', '--nc-cyan', '--nc-magenta', '--nc-green',
  '--pur', '--pink', '--blue', '--green', '--warn',
  '--card', '--panel', '--bg', '--line', '--txt', '--sub',
]

// 各类型语义 schema（不含通用样式，通用样式由面板自动追加）
const textSchema = [
  { key: 'props.content', label: '文字内容', control: 'textarea' },
  { key: 'style.fontSize', label: '字号', control: 'slider', min: 10, max: 72 },
  { key: 'style.fontWeight', label: '字重', control: 'select', options: [400, 600, 800, 900] },
  { key: 'style.textAlign', label: '对齐', control: 'select', options: ['left', 'center', 'right'] },
  { key: 'style.letterSpacing', label: '字距', control: 'slider', min: 0, max: 20 },
  { key: 'style.lineHeight', label: '行高', control: 'slider', min: 1, max: 2.5, step: 0.1 },
  { key: 'style.textShadow', label: '文字描边', control: 'text' },
]

const titleSchema = [
  { key: 'props.content', label: '标题文本', control: 'textarea' },
  { key: 'props.level', label: '级别', control: 'select', options: [1, 2, 3] },
  { key: 'style.fontSize', label: '字号', control: 'slider', min: 14, max: 48 },
  { key: 'style.fontWeight', label: '字重', control: 'select', options: [600, 800, 900] },
  { key: 'style.transform', label: '斜切', control: 'text', placeholder: 'skewX(-6deg)' },
  { key: 'style.textShadow', label: '文字描边', control: 'text' },
]

const buttonSchema = [
  { key: 'props.content', label: '按钮文本', control: 'text' },
  { key: 'props.actionType', label: '点击行为', control: 'select', options: ['none', 'openDialog', 'switchSubpage', 'switchPage', 'toast'] },
  { key: 'props.actionTarget', label: '目标(dialogId/subpageId/页面ID)', control: 'text' },
  { key: 'props.actionMessage', label: '提示文案(toast)', control: 'text' },
  { key: 'style.background', label: '背景色', control: 'color' },
  { key: 'style.borderRadius', label: '圆角', control: 'slider', min: 0, max: 24 },
  { key: 'style.boxShadow', label: '阴影', control: 'text' },
]

const inputSchema = [
  { key: 'props.placeholder', label: '占位文本', control: 'text' },
  { key: 'style.border', label: '边框', control: 'text', placeholder: '1px solid var(--line)' },
]

const statSchema = [
  { key: 'props.title', label: '标题', control: 'text' },
  { key: 'props.value', label: '数值', control: 'text' },
  { key: 'props.unit', label: '单位', control: 'text' },
  { key: 'props.sub', label: '副标题', control: 'text' },
]

const tagSchema = [
  { key: 'props.content', label: '文本内容', control: 'text' },
  { key: 'props.variant', label: '颜色变体', control: 'select', options: ['default', 'cyan', 'magenta', 'green', 'ghost'] },
]

const progressSchema = [
  { key: 'props.percent', label: '百分比', control: 'slider', min: 0, max: 100 },
  { key: 'props.color', label: '颜色', control: 'color' },
]

const imgSchema = [
  { key: 'props.src', label: '图片地址(占位)', control: 'text' },
  { key: 'style.filter', label: '双色调滤镜', control: 'text', placeholder: 'grayscale(1)' },
]

const menuSchema = [
  { key: 'props.content', label: '菜单文本', control: 'text' },
  { key: 'props.actionType', label: '点击行为', control: 'select', options: ['none', 'menu', 'switchPage', 'toast'] },
  { key: 'props.actionTarget', label: '目标(页面ID/路由)', control: 'text' },
]

const dialogSchema = [
  { key: 'props.title', label: '弹窗标题', control: 'text' },
  { key: 'props.dialogId', label: '弹窗ID', control: 'text' },
  { key: 'props.width', label: '宽度', control: 'text', placeholder: '480px' },
]

const subpageSchema = [
  { key: 'props.title', label: '子页面标题', control: 'text' },
  { key: 'props.subpageId', label: '子页面ID', control: 'text' },
]

// 类型 → 渲染组件 + 语义 schema（容器类共用 ContainerNode）
export const nodeTypes = {
  page: { component: ContainerNode, schema: [{ key: 'style.background', label: '背景色', control: 'color' }, { key: 'style.padding', label: '内边距', control: 'text' }] },
  row: { component: ContainerNode, schema: [{ key: 'style.gap', label: '间距', control: 'text', placeholder: '16px' }, { key: 'style.padding', label: '内边距', control: 'text' }] },
  col: { component: ContainerNode, schema: [{ key: 'style.gap', label: '间距', control: 'text', placeholder: '16px' }, { key: 'style.padding', label: '内边距', control: 'text' }] },
  navbar: { component: ContainerNode, schema: [{ key: 'style.background', label: '背景色', control: 'color' }, { key: 'style.height', label: '高度', control: 'text' }] },
  sidebar: { component: ContainerNode, schema: [] },
  card: { component: ContainerNode, schema: [{ key: 'props.title', label: '标题文本', control: 'text' }, { key: 'style.background', label: '背景色', control: 'color' }, { key: 'style.borderRadius', label: '圆角', control: 'slider', min: 0, max: 24 }, { key: 'style.boxShadow', label: '阴影', control: 'text' }] },
  dialog: { component: ContainerNode, schema: dialogSchema },
  subpage: { component: ContainerNode, schema: subpageSchema },
  space: { component: ContainerNode, schema: [{ key: 'style.width', label: '宽度', control: 'text' }, { key: 'style.height', label: '高度', control: 'text' }] },
  divider: { component: ContainerNode, schema: [{ key: 'style.background', label: '颜色', control: 'color' }, { key: 'style.height', label: '粗细', control: 'text' }] },
  text: { component: TextNode, schema: textSchema },
  title: { component: TextNode, schema: titleSchema },
  button: { component: ButtonNode, schema: buttonSchema },
  input: { component: InputNode, schema: inputSchema },
  statcard: { component: StatCardNode, schema: statSchema },
  tag: { component: TagNode, schema: tagSchema },
  progress: { component: ProgressNode, schema: progressSchema },
  img: { component: ImgNode, schema: imgSchema },
  menuitem: { component: MenuItemNode, schema: menuSchema },
  unknown: { component: ContainerNode, schema: [] },
}

export function getNodeSchema(type) {
  const meta = nodeTypes[type] || nodeTypes.unknown
  return [...meta.schema, ...commonStyleSchema.map((s) => ({ ...s, common: true }))]
}

/**
 * 六维模型的展示映射（前后端共用约定的唯一前端副本）
 *
 * 后端 `constants.DIMENSIONS[].key` 是 six 个稳定英文键，
 * `WRONG_CATEGORIES` 又把「主要失分维度」映射成中文分类名。
 * 前端需要在这三种表示之间对照，因此集中放在这里，避免各页面各写一份。
 */

/** 维度 key（含错题分类的中文名）→ 维度色码 class 后缀 */
const KEY_TO_SLUG = {
  // 英文 key
  professional: 'professional',
  logic: 'logic',
  expression: 'expression',
  matching: 'matching',
  adaptability: 'adaptability',
  culture_fit: 'culture-fit',
  // 错题本分类（后端 WRONG_CATEGORIES 的中文值）
  专业类: 'professional',
  逻辑类: 'logic',
  表达类: 'expression',
  匹配类: 'matching',
  应变类: 'adaptability',
  文化类: 'culture-fit',
  // 维度中文名（报告里按 name 传参时兜底）
  专业能力: 'professional',
  逻辑思维: 'logic',
  表达沟通: 'expression',
  岗位匹配度: 'matching',
  应变能力: 'adaptability',
  文化适配: 'culture-fit',
}

/**
 * 取维度色码 class（不含前导点）。
 * 用法：`chip chip--dim ${dimClass(d.key)}`
 * 无法识别时返回 ''，此时 .chip--dim 会退回中性灰，不会出错。
 */
export function dimClass(keyOrName) {
  const slug = KEY_TO_SLUG[String(keyOrName || '').trim()]
  return slug ? `dim-${slug}` : ''
}

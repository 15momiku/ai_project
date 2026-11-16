/**
 * 投递进度字典（岗位信息汇总）
 *
 * ⚠️ 这份字典与后端 `backend/app/constants.py` 的 `JOB_STATUS_META` /
 * `JOB_STATUS_GROUPS` / `JOB_STAGES` **逐字段镜像**：
 *   · 服务端用它校验写入（非法状态直接 400，不靠前端隐藏下拉）；
 *   · 前端用它算徽章颜色、阶段点数、漏斗与分布条的展示口径。
 *
 * 与本项目 `constants/dimensions.js` 的做法一致：镜像 + 显式注释，
 * 而不是各写一套「看起来差不多」的定义。改动时**两边必须同时改**，
 * 否则会出现「界面能选、后端拒绝」这种最难查的不一致。
 *
 * 字段含义：
 *   type  → none 未投递 / active 进行中 / fail 已挂 / pass 终态通过
 *   stage → 所处环节（1..6）；7 = 已走完全部环节；0 = 未投递
 *   dots  → 阶段点数（0..6），用于表格徽章里的进度点可视化
 */

export const JOB_STATUS_META = {
  未投递: { type: 'none', stage: 0, dots: 0 },
  简历初筛: { type: 'active', stage: 1, dots: 1 },
  初筛挂: { type: 'fail', stage: 1, dots: 1 },
  已测评: { type: 'active', stage: 2, dots: 2 },
  测评挂: { type: 'fail', stage: 2, dots: 2 },
  已AI面: { type: 'active', stage: 3, dots: 3 },
  AI面挂: { type: 'fail', stage: 3, dots: 3 },
  已初面: { type: 'active', stage: 4, dots: 4 },
  初面挂: { type: 'fail', stage: 4, dots: 4 },
  已二面: { type: 'active', stage: 5, dots: 5 },
  二面挂: { type: 'fail', stage: 5, dots: 5 },
  已终面: { type: 'active', stage: 6, dots: 6 },
  终面挂: { type: 'fail', stage: 6, dots: 6 },
  已OC: { type: 'pass', stage: 7, dots: 6 },
  已Offer: { type: 'pass', stage: 7, dots: 6 },
}

/** 六个筛选/面试环节（漏斗顺序） */
export const JOB_STAGES = ['简历初筛', '测评', 'AI面', '初面', '二面', '终面']

/** 进度选择器里的分组顺序 */
export const JOB_STATUS_GROUPS = [
  { label: '未投递', items: ['未投递'] },
  { label: '筛选中', items: ['简历初筛', '已测评'] },
  { label: '面试中', items: ['已AI面', '已初面', '已二面', '已终面'] },
  { label: '已挂', items: ['初筛挂', '测评挂', 'AI面挂', '初面挂', '二面挂', '终面挂'] },
  { label: '终态', items: ['已OC', '已Offer'] },
]

export const JOB_STATUS_LIST = Object.keys(JOB_STATUS_META)

/** 「未投递」= 没有记录（后端收到它会删掉那一行），因此它是默认值而不是一种存储状态 */
export const JOB_STATUS_DEFAULT = '未投递'

/** 状态分段控件（列表页顶部的 全部/未投递/进行中/已挂/Offer） */
export const JOB_SEGMENTS = [
  { key: 'all', label: '全部' },
  { key: 'none', label: '未投递' },
  { key: 'active', label: '进行中' },
  { key: 'fail', label: '已挂' },
  { key: 'pass', label: 'Offer' },
]

/** 六个筛选维度：前三个是单值，后三个是多值数组 */
export const JOB_FILTERS = [
  { key: 'nature', label: '雇主类型', multi: false },
  { key: 'industry', label: '行业', multi: false },
  { key: 'audience', label: '毕业届别', multi: false },
  { key: 'locations', label: '工作地点', multi: true },
  { key: 'majors', label: '专业要求', multi: true },
  { key: 'positions', label: '招聘岗位', multi: true },
]

/** 投递进度徽章 / 进度点的配色语义（只表达状态类型，不为 15 个状态各配一色）
 *  颜色本身定义在 job-hub.css 的 .jh-badge--{none|active|fail|pass} 里，
 *  这里**只留语义类型**：在 JS 里再存一份 hex 就是第二个事实来源，
 *  迟早会出现「JS 说是蓝的、CSS 改成了紫的」。 */

export function jobMetaOf(status) {
  return JOB_STATUS_META[status] || JOB_STATUS_META[JOB_STATUS_DEFAULT]
}

export const jobTypeOf = (status) => jobMetaOf(status).type
export const jobStageOf = (status) => jobMetaOf(status).stage
export const jobDotsOf = (status) => jobMetaOf(status).dots
export const isApplied = (status) => !!status && status !== JOB_STATUS_DEFAULT

/** 把一个值按「同一维度内为或、不同维度之间为与」的口径判断是否命中多选筛选 */
export function matchFilterValue(value, selected) {
  if (!selected || selected.size === 0) return true
  if (Array.isArray(value)) return value.some((v) => selected.has(v))
  return selected.has(value || '')
}

/** 岗位机会的一条记录 → 全文检索用的可搜索文本（企业/性质/行业/对象/地点/专业/岗位/备注/进度） */
export function searchableText(rec) {
  return [
    rec?.company,
    rec?.nature,
    rec?.industry,
    rec?.audience,
    rec?.note,
    rec?.progress,
    ...(rec?.locations || []),
    ...(rec?.majors || []),
    ...(rec?.positions || []),
  ]
    .filter(Boolean)
    .join(' ')
    .toLowerCase()
}

/**
 * 岗位信息汇总用的格式化小工具。
 *
 * 只放纯函数：这些计算同时被列表页与统计页用到，写在页面里就会出现两份
 * 「相对日期」实现，早晚会出现一处四舍五入、另一处不四舍五入的差异。
 */

/** 今天（本地时区）的 YYYY-MM-DD，用于导出文件名与预览里的「更新时间」 */
export function todayStr() {
  const t = new Date()
  return `${t.getFullYear()}-${String(t.getMonth() + 1).padStart(2, '0')}-${String(t.getDate()).padStart(2, '0')}`
}

/**
 * 把一个 YYYY-MM-DD 说成人话：今天 / 昨天 / N 天前 / N 个月前 / N 年前。
 *
 * 岗位信息是「按天」粒度的事实，相对说法比绝对日期更容易判断新鲜度
 * （「14 天前」一眼就知道这条公告可能已经过期）。
 */
export function relativeDay(dateStr) {
  if (!dateStr) return ''
  const d = new Date(`${dateStr}T00:00:00`)
  if (Number.isNaN(d.getTime())) return ''
  const now = new Date()
  now.setHours(0, 0, 0, 0)
  const diff = Math.round((now - d) / 86400000)
  if (diff === 0) return '今天'
  if (diff === 1) return '昨天'
  if (diff < 0) return `${Math.abs(diff)} 天后`
  if (diff < 30) return `${diff} 天前`
  if (diff < 365) return `${Math.round(diff / 30)} 个月前`
  return `${Math.round(diff / 365)} 年前`
}

/** 距今天数（负数代表未来）；用于「超过 3 周未更新」的淡化提示 */
export function daysOld(dateStr) {
  if (!dateStr) return 0
  const d = new Date(`${dateStr}T00:00:00`)
  if (Number.isNaN(d.getTime())) return 0
  const now = new Date()
  now.setHours(0, 0, 0, 0)
  return Math.round((now - d) / 86400000)
}

/** 把 URL 补全成可直接打开的地址（用户常只填 example.com） */
export function fullUrl(url) {
  const raw = String(url || '').trim()
  if (!raw) return ''
  return /^https?:\/\//i.test(raw) ? raw : `https://${raw}`
}

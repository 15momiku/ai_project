import { IconChevron } from '../icons'

/** 每页条数：汇总列表与投递统计共用（岗位库动辄上百条，一页 30 条在手机上也要滑很久） */
export const JOB_PAGE_SIZE = 30

/**
 * 分页控件（汇总列表 / 投递统计明细共用）。
 *
 * 三个刻意的取舍：
 *
 * 1. **不做「每页 N 条」选择器**。页大小只有 30 一个值，给用户一个开关只是多一个
 *    要维护的状态；真要更多，用搜索与筛选缩小范围才是对的交互。
 * 2. **页码按钮按窗口展开**（当前页前后各 2 页 + 首尾页），避免 20 页时出现一排数字
 *    把工具栏挤爆；首尾用省略号占位。
 * 3. **翻页后不自动滚回顶部**：这里的列表本身不高（有筛选栏与表头），
 *    强行滚动反而会让「我点到哪了」失去参照。分页按钮就在列表下方，视线不需要移动很远。
 *
 * 无障碍：`<nav aria-label>` + `aria-current="page"` 标记当前页，
 * 上一页 / 下一页在首尾页是 disabled（而不是消失，避免布局跳动）。
 */
export default function JobPager({ page, total, onChange, label = '列表分页' }) {
  const pages = Math.max(1, Math.ceil(total / JOB_PAGE_SIZE))
  const current = Math.min(Math.max(1, page), pages)
  if (total <= JOB_PAGE_SIZE) {
    return (
      <p className="jh-pager jh-pager--single">
        共 <strong className="mono">{total}</strong> 条
      </p>
    )
  }

  const start = (current - 1) * JOB_PAGE_SIZE + 1
  const end = Math.min(total, current * JOB_PAGE_SIZE)

  /* 页码窗口：首尾页永远可见，当前页左右各 2 页 */
  const nums = []
  for (let i = 1; i <= pages; i++) {
    if (i === 1 || i === pages || Math.abs(i - current) <= 2) nums.push(i)
    else if (nums[nums.length - 1] !== '...') nums.push('...')
  }

  return (
    <nav className="jh-pager" aria-label={label}>
      <span className="jh-pager-stat">
        第 <strong className="mono">{start}</strong>–<strong className="mono">{end}</strong> 条 ·
        共 <strong className="mono">{total}</strong> 条 · 每页 {JOB_PAGE_SIZE} 条
      </span>

      <div className="jh-pager-btns">
        <button
          type="button"
          className="jh-page-btn"
          onClick={() => onChange(current - 1)}
          disabled={current <= 1}
          aria-label="上一页"
        >
          <IconChevron size={15} className="jh-page-chev jh-page-chev--prev" />
          <span className="jh-page-text">上一页</span>
        </button>

        {nums.map((n, i) =>
          n === '...' ? (
            <span className="jh-page-gap" key={`gap-${i}`} aria-hidden="true">
              …
            </span>
          ) : (
            <button
              key={n}
              type="button"
              className={`jh-page-btn jh-page-num ${n === current ? 'on' : ''}`}
              aria-current={n === current ? 'page' : undefined}
              aria-label={`第 ${n} 页`}
              onClick={() => onChange(n)}
            >
              {n}
            </button>
          )
        )}

        <button
          type="button"
          className="jh-page-btn"
          onClick={() => onChange(current + 1)}
          disabled={current >= pages}
          aria-label="下一页"
        >
          <span className="jh-page-text">下一页</span>
          <IconChevron size={15} className="jh-page-chev" />
        </button>
      </div>
    </nav>
  )
}

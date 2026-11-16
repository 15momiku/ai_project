import { jobDotsOf, jobTypeOf } from '../../constants/jobStatus'
import { IconChevron } from '../icons'

/**
 * 投递进度徽章：**状态类型决定颜色，阶段深度用 6 格进度点表达**。
 *
 * 为什么不给 15 个状态各配一种颜色：一是配色会失控（15 个色相谁也记不住），
 * 二是「初筛挂 / 测评挂 / 二面挂」的区别本来就不在颜色上，而在**走到哪一步**。
 * 用「灰=未投递 / 蓝=进行中 / 红=已挂 / 绿=终态」四类色 + 进度点表示深度，
 * 与 Apple HIG 的做法一致，扫读时更快。
 *
 * `interactive` 为 true 时渲染成按钮（点开进度选择器）；
 * 否则是纯展示的静态徽章（统计页导出前、只读场景）。
 */
export default function JobStatusBadge({
  status,
  interactive = false,
  onClick,
  size = 'md',
  title,
}) {
  const type = jobTypeOf(status)
  const dots = jobDotsOf(status)
  const label = `投递进度：${status}`

  const inner = (
    <>
      <i className="jh-bdot" aria-hidden="true" />
      <span className="jh-badge-text">{status}</span>
      {dots > 0 && (
        <span className="jh-dots" aria-hidden="true">
          {[1, 2, 3, 4, 5, 6].map((i) => (
            <i key={i} className={i <= dots ? 'on' : ''} />
          ))}
        </span>
      )}
      {interactive && <IconChevron size={13} className="jh-badge-chev" />}
    </>
  )

  if (!interactive) {
    return (
      <span className={`jh-badge jh-badge--${type} jh-badge--${size}`} title={title || label}>
        {inner}
      </span>
    )
  }

  return (
    <button
      type="button"
      className={`jh-badge jh-badge--${type} jh-badge--${size} jh-badge--btn`}
      onClick={onClick}
      title={title || `${label}（点击修改）`}
      aria-haspopup="dialog"
      aria-label={`修改投递进度，当前为${status}`}
    >
      {inner}
    </button>
  )
}

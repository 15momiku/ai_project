import { IconTrendUp } from './icons'

/**
 * 得分趋势折线图（手写 SVG，无第三方图表库）
 *
 * 两处复用，因此抽成组件：
 *  · 报告页底部「历史进步」——本场在整条曲线里的位置；
 *  · 「我的面试 · 面试报告」——近 10 场的评分可视化。
 *
 * props
 *  - items:      [{ session_id, date, score, job_title }] 按时间**升序**（左旧右新）
 *  - onPick:     可选，点击某个点回调该条数据（用于跳到那场的报告）
 *  - emptyTitle / emptyDesc: 空态文案
 *  - ariaLabel:  无障碍名称
 *
 * 无障碍：提供 onPick 时每个点是可聚焦的操作（role=button + Enter/Space），
 * 而不是只能用鼠标点的图形；图表本身给 role="img" + aria-label 概括。
 */
export default function ScoreTrend({
  items = [],
  onPick,
  emptyTitle = '还没有历史曲线',
  emptyDesc = '完成第 2 次模拟面试后，这里会显示你的分数变化曲线。',
  ariaLabel = '历史得分曲线',
}) {
  if (!items.length) {
    return (
      <div className="empty">
        <IconTrendUp size={28} style={{ color: 'var(--text-mute)' }} />
        <div className="empty-title">{emptyTitle}</div>
        <div className="empty-desc">{emptyDesc}</div>
      </div>
    )
  }

  const W = 660
  const H = 200
  const padL = 34
  const padR = 16
  const padT = 22
  const padB = 30
  const scores = items.map((h) => Number(h.score) || 0)
  const max = Math.max(10, ...scores)
  const min = Math.min(0, ...scores)
  const n = items.length
  const x = (i) => padL + ((W - padL - padR) * i) / Math.max(1, n - 1)
  const y = (s) => padT + (H - padT - padB) * (1 - (s - min) / Math.max(0.1, max - min))
  const line = items.map((h, i) => `${x(i)},${y(Number(h.score) || 0)}`).join(' ')
  const area = `${padL},${H - padB} ${line} ${x(n - 1)},${H - padB}`
  const fmt = (v) => (Number.isFinite(Number(v)) ? Number(v).toFixed(1) : '—')

  const activate = (item) => {
    if (item && onPick) onPick(item)
  }

  return (
    <svg className="trend-svg" viewBox={`0 0 ${W} ${H}`} role="img" aria-label={ariaLabel}>
      <defs>
        <linearGradient id="trendFill" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor="var(--primary)" stopOpacity="0.22" />
          <stop offset="100%" stopColor="var(--primary)" stopOpacity="0" />
        </linearGradient>
      </defs>
      {[0, 5, 10].map((v) => (
        <g key={v}>
          <line x1={padL} y1={y(v)} x2={W - padR} y2={y(v)} stroke="var(--line)" strokeDasharray="3 4" />
          <text
            x={6}
            y={y(v) + 3}
            style={{ fontSize: 9, fill: 'var(--text-mute)', fontFamily: 'var(--font-mono)' }}
          >
            {v}
          </text>
        </g>
      ))}
      <polygon points={area} fill="url(#trendFill)" />
      <polyline points={line} fill="none" stroke="var(--primary)" strokeWidth="2.4" strokeLinejoin="round" />
      {items.map((h, i) => {
        const cx = x(i)
        const cy = y(Number(h.score) || 0)
        const label = `${h.date || `第 ${i + 1} 次`} ${h.job_title || ''} ${fmt(h.score)} 分`
        return (
          <g
            key={h.session_id || i}
            className={onPick ? 'trend-hit' : undefined}
            role={onPick ? 'button' : undefined}
            tabIndex={onPick ? 0 : undefined}
            aria-label={onPick ? `查看 ${label} 的报告` : undefined}
            onClick={onPick ? () => activate(h) : undefined}
            onKeyDown={
              onPick
                ? (e) => {
                    if (e.key === 'Enter' || e.key === ' ') {
                      e.preventDefault()
                      activate(h)
                    }
                  }
                : undefined
            }
          >
            {/* 命中区域：曲线上的点很小，单独放一个透明大圆做触控热区 */}
            {onPick && <circle cx={cx} cy={cy} r="14" fill="transparent" />}
            <circle
              className="trend-dot"
              cx={cx}
              cy={cy}
              r="4.6"
              fill="var(--surface)"
              stroke="var(--primary)"
              strokeWidth="2"
            />
            <text
              x={cx}
              y={cy - 10}
              textAnchor="middle"
              style={{ fontSize: 10, fill: 'var(--fg-title)', fontFamily: 'var(--font-mono)' }}
            >
              {fmt(h.score)}
            </text>
            <text x={cx} y={H - 10} textAnchor="middle" style={{ fontSize: 9, fill: 'var(--text-mute)' }}>
              {(h.date || '').slice(5) || `第${i + 1}次`}
            </text>
          </g>
        )
      })}
    </svg>
  )
}

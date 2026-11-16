import { useEffect, useState } from 'react'

/**
 * 环形进度（匹配度 / 总分 / Offer 概率）
 * 手写 SVG，带渐变色描边与入场动画（transform/opacity 之外的 stroke 过渡 ≤ 900ms）
 */
export default function ScoreRing({
  value = 0,
  size = 132,
  stroke = 10,
  label = '',
  sub = '',
  from = 'var(--primary)',
  to = 'var(--primary-2)',
  center = null,
}) {
  const v = Math.max(0, Math.min(100, Number(value) || 0))
  const r = (size - stroke) / 2
  const c = 2 * Math.PI * r
  const [shown, setShown] = useState(0)
  // 渐变 id 不能带 var()/括号等非法字符，按入参做一个稳定的短哈希
  const gid = `ring-${String(from)}-${String(to)}`.replace(/[^a-zA-Z0-9-]/g, '')

  useEffect(() => {
    const t = setTimeout(() => setShown(v), 60)
    return () => clearTimeout(t)
  }, [v])

  const offset = c - (c * shown) / 100

  return (
    <div style={{ position: 'relative', width: size, height: size, flex: 'none' }}>
      <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`} role="img" aria-label={label}>
        <defs>
          <linearGradient id={gid} x1="0" y1="0" x2="1" y2="1">
            <stop offset="0%" stopColor={from} />
            <stop offset="100%" stopColor={to} />
          </linearGradient>
        </defs>
        <circle
          cx={size / 2}
          cy={size / 2}
          r={r}
          fill="none"
          stroke="var(--fill-4)"
          strokeWidth={stroke}
        />
        <circle
          cx={size / 2}
          cy={size / 2}
          r={r}
          fill="none"
          stroke={`url(#${gid})`}
          strokeWidth={stroke}
          strokeLinecap="round"
          strokeDasharray={c}
          strokeDashoffset={offset}
          transform={`rotate(-90 ${size / 2} ${size / 2})`}
          style={{ transition: 'stroke-dashoffset 900ms cubic-bezier(.22,1,.36,1)' }}
        />
      </svg>
      <div
        style={{
          position: 'absolute',
          inset: 0,
          display: 'flex',
          flexDirection: 'column',
          alignItems: 'center',
          justifyContent: 'center',
          gap: 2,
        }}
      >
        {center ?? (
          <>
            <div
              className="mono"
              style={{ fontSize: size * 0.26, fontWeight: 700, lineHeight: 1.1, color: 'var(--fg-title)' }}
            >
              {Math.round(v)}
              <span style={{ fontSize: size * 0.12, color: 'var(--text-mute)' }}>%</span>
            </div>
            {label && <div style={{ fontSize: 11.5, color: 'var(--text-dim)' }}>{label}</div>}
          </>
        )}
        {sub && (
          <div style={{ fontSize: 10.5, color: 'var(--text-mute)', padding: '0 8px', textAlign: 'center' }}>
            {sub}
          </div>
        )}
      </div>
    </div>
  )
}

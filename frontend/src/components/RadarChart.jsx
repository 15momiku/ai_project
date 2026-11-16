import { useMemo } from 'react'
import { dimClass } from '../constants/dimensions'

/**
 * 六维雷达图（手写 SVG，不依赖任何图表库）
 *
 * props
 *  - dimensions: [{ key, name, score }]        用户得分
 *  - target:     { [name|key]: number }        目标岗位均分（虚线多边形）
 *  - max:        分值上限，默认 10
 *  - active:     当前选中的维度 key（点击轴标签/顶点触发 onSelect）
 *  - onSelect:   (key) => void
 *
 * 辨识度：每个维度用 global.css 的维度色码着色（与报告/错题本同色），
 * 顶点用该维度的颜色区分，比单色多边形更容易定位「哪一项弱」。
 */
export default function RadarChart({
  dimensions = [],
  target = null,
  max = 10,
  active = '',
  onSelect,
  height = 300,
}) {
  const W = 340
  const H = height
  const cx = W / 2
  const cy = H / 2 + 4
  const R = Math.min(W, H) / 2 - 46

  const items = useMemo(() => {
    const list = Array.isArray(dimensions) ? dimensions : []
    if (!list.length) return []
    return list.map((d, i) => {
      const angle = -Math.PI / 2 + (i * 2 * Math.PI) / list.length
      return {
        ...d,
        angle,
        score: Number(d?.score ?? 0) || 0,
        targetScore: Number(target?.[d?.name] ?? target?.[d?.key] ?? 0) || 0,
      }
    })
  }, [dimensions, target])

  if (!items.length) {
    return (
      <div className="empty" style={{ padding: 20 }}>
        <div className="empty-desc">暂无维度数据</div>
      </div>
    )
  }

  const pt = (angle, ratio) => {
    const r = R * Math.max(0, Math.min(1, ratio))
    return [cx + Math.cos(angle) * r, cy + Math.sin(angle) * r]
  }

  const poly = (getRatio) =>
    items.map((d) => pt(d.angle, getRatio(d)).join(',')).join(' ')

  const rings = [0.2, 0.4, 0.6, 0.8, 1]

  return (
    <div>
      <svg className="radar-svg" viewBox={`0 0 ${W} ${H}`} role="img" aria-label="六维能力雷达图">
        <defs>
          <linearGradient id="rcFill" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="var(--primary)" stopOpacity="0.26" />
            <stop offset="100%" stopColor="var(--primary-2)" stopOpacity="0.08" />
          </linearGradient>
        </defs>

        {/* 网格环 */}
        {rings.map((r, i) => (
          <polygon
            key={i}
            points={poly(() => r)}
            fill="none"
            stroke="var(--line)"
            strokeWidth={i === rings.length - 1 ? 1.2 : 0.8}
          />
        ))}

        {/* 轴线 */}
        {items.map((d) => {
          const [x, y] = pt(d.angle, 1)
          return (
            <line
              key={d.key || d.name}
              x1={cx}
              y1={cy}
              x2={x}
              y2={y}
              stroke="var(--line-faint)"
              strokeWidth="0.9"
            />
          )
        })}

        {/* 目标岗位均分（虚线，中性灰以免与得分混淆） */}
        {items.some((d) => d.targetScore > 0) && (
          <polygon
            points={poly((d) => d.targetScore / max)}
            fill="none"
            stroke="var(--text-mute)"
            strokeOpacity="0.6"
            strokeWidth="1.6"
            strokeDasharray="5 4"
          />
        )}

        {/* 用户得分（实线 + 浅色填充） */}
        <polygon
          points={poly((d) => d.score / max)}
          fill="url(#rcFill)"
          stroke="var(--primary)"
          strokeWidth="2.2"
          strokeLinejoin="round"
        />

        {/* 顶点 + 轴标签（按维度着色，便于定位薄弱维度） */}
        {items.map((d) => {
          const [x, y] = pt(d.angle, d.score / max)
          const [lx, ly] = pt(d.angle, 1.24)
          const cos = Math.cos(d.angle)
          const anchor = Math.abs(cos) < 0.25 ? 'middle' : cos > 0 ? 'start' : 'end'
          const isOn = active && (active === d.key || active === d.name)
          const cls = dimClass(d.key || d.name)
          return (
            <g key={`v-${d.key || d.name}`} className={cls}>
              {/* 同色光晕：选中时让该维度在整张图里跳出来 */}
              {isOn && <circle cx={x} cy={y} r={11} fill="var(--dim-c)" opacity="0.16" />}
              <circle
                cx={x}
                cy={y}
                r={isOn ? 6 : 4.2}
                fill="var(--dim-c, var(--primary))"
                stroke="var(--surface)"
                strokeWidth="2"
                style={{ cursor: 'pointer', transition: 'r .2s ease' }}
                onClick={() => onSelect?.(d.key || d.name)}
              />
              <text
                className={`radar-label ${isOn ? 'on' : ''}`}
                x={lx}
                y={ly}
                textAnchor={anchor}
                dominantBaseline="middle"
                style={isOn ? { fill: 'var(--dim-c)', fontWeight: 700 } : undefined}
                onClick={() => onSelect?.(d.key || d.name)}
              >
                {d.name}
              </text>
              <text
                className="radar-label"
                x={lx}
                y={ly + 13}
                textAnchor={anchor}
                dominantBaseline="middle"
                style={{
                  fontSize: 10.5,
                  fill: isOn ? 'var(--dim-c)' : 'var(--dim-c, var(--text-mute))',
                  fontWeight: 600,
                  fontFamily: 'var(--font-mono)',
                }}
              >
                {(Number(d.score) || 0).toFixed(1)}
              </text>
            </g>
          )
        })}
      </svg>

      <div className="radar-legend">
        <span>
          <i style={{ background: 'var(--primary)' }} />
          我的得分
        </span>
        <span>
          <i style={{ background: 'var(--text-mute)', opacity: 0.6 }} />
          目标岗位均分
        </span>
        <span className="mute-2">点击维度查看点评</span>
      </div>
    </div>
  )
}

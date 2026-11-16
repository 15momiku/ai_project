/**
 * 通用区块卡片（带序号 / 标题 / 描述 / 右侧操作位）
 */
export default function SectionCard({
  index,
  title,
  desc,
  right,
  children,
  className = '',
  flat = false,
  style,
}) {
  return (
    <section className={`card ${flat ? 'card--flat' : ''} ${className}`} style={style}>
      {(title || right) && (
        <div className="card-head">
          <div>
            <div className="card-title">
              {index !== undefined && index !== null && <span className="sec-idx">{index}</span>}
              <span>{title}</span>
            </div>
            {desc && <div className="sec-desc">{desc}</div>}
          </div>
          {right && <div className="row gap-8">{right}</div>}
        </div>
      )}
      {children}
    </section>
  )
}

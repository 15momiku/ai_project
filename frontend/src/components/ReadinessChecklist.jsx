import { IconCheckCircle, IconKeyboard, IconMic, IconPlay, IconTarget, IconUpload } from './icons'
import '../styles/readiness.css'

/**
 * 首次进入的准备度清单（体验增强 §1）。
 *
 * 设计要点：
 * - 三态清单（已就绪 / 待完成），每一项都可点回修改，而不是一条纯进度条；
 * - 未完成时主 CTA **必须给出具体原因**，不允许只把按钮灰掉了事；
 * - 三项齐备后折叠为一行摘要，避免长期占据首屏；
 * - 空态（还没有任何简历）直接给出「先用示例跑一遍」的次按钮。
 *
 * 组件本身不做任何请求：数据与动作全部由父页面注入，便于单独验证四态。
 */
export default function ReadinessChecklist({
  hasResume = false,
  hasJd = false,
  deviceChecked = false,
  loading = false,
  seeding = false,
  onGoSetup,
  onGoDeviceCheck,
  onDemo,
  onDismiss,
}) {
  /* ---- 加载态：骨架屏三行（prefers-reduced-motion 下由 CSS 关掉微光动画）---- */
  if (loading) {
    return (
      <section className="card readiness" aria-busy="true" aria-live="polite">
        <div className="skeleton sk-line sk-line--40" />
        <div className="skeleton sk-line" />
        <div className="skeleton sk-line sk-line--60" />
        <span className="sr-only">正在检查你的准备情况…</span>
      </section>
    )
  }

  const items = [
    {
      key: 'resume',
      done: hasResume,
      Icon: IconUpload,
      title: '上传简历',
      todoDesc: '支持 PDF，也可直接粘贴文本；解析后可以逐项修改',
      doneDesc: '简历已解析，可用于匹配与出题',
      action: '去上传',
    },
    {
      key: 'jd',
      done: hasJd,
      Icon: IconTarget,
      title: '选择目标岗位',
      todoDesc: '粘贴 JD 或选择目标公司，匹配度与提问策略都基于它',
      doneDesc: '目标岗位已就绪',
      action: '去选择',
    },
    {
      key: 'device',
      done: deviceChecked,
      Icon: IconMic,
      title: '测试麦克风与语音',
      todoDesc: '约 30 秒：检查麦克风与面试官语音，避免答到一半才发现没声音',
      doneDesc: '设备已通过自检',
      action: '开始自检',
    },
  ]
  const doneCount = items.filter((i) => i.done).length
  const allDone = doneCount === items.length
  const firstTodo = items.find((i) => !i.done)

  /* ---- 完成态：折叠成一行摘要 + 放大的主 CTA ---- */
  if (allDone) {
    return (
      <section className="card readiness readiness--done fade-up">
        <div className="row row--between row--wrap gap-12">
          <div className="row gap-8 readiness-done-line">
            <IconCheckCircle size={18} />
            <span>
              准备就绪：简历、目标岗位、麦克风与语音都已确认
            </span>
          </div>
          <button className="btn btn--primary btn--lg" onClick={onGoSetup}>
            <IconPlay size={17} /> 开始模拟面试
          </button>
        </div>
      </section>
    )
  }

  return (
    <section className="card readiness fade-up" aria-labelledby="readiness-title">
      <div className="readiness-head">
        <div>
          <h2 className="readiness-title" id="readiness-title">
            开始你的第一次模拟面试
          </h2>
          <p className="readiness-sub">
            {hasResume
              ? '三步大约 3 分钟。随时可以回来继续，准备到哪一步都不会丢。'
              : '还没有简历？可先用示例跑一遍，30 秒就能看到完整流程。'}
          </p>
        </div>
        <div className="readiness-head-right">
          <span className="chip chip--primary mono readiness-count">
            {doneCount}/{items.length} 已完成
          </span>
          {onDismiss ? (
            <button className="link-btn readiness-dismiss" onClick={onDismiss}>
              不再显示
            </button>
          ) : null}
        </div>
      </div>

      <ul className="readiness-list">
        {items.map((it) => {
          const Icon = it.Icon
          return (
            <li className={`readiness-item${it.done ? ' is-done' : ''}`} key={it.key}>
              <span className="readiness-mark" aria-hidden="true">
                {it.done ? <IconCheckCircle size={18} /> : <Icon size={18} />}
              </span>
              <span className="readiness-text">
                <span className="readiness-item-title">
                  {it.title}
                  <span className="sr-only">{it.done ? '：已完成' : '：待完成'}</span>
                </span>
                <span className="readiness-item-desc">{it.done ? it.doneDesc : it.todoDesc}</span>
              </span>
              {it.key === 'device' ? (
                <button className="btn btn--ghost btn--sm" onClick={onGoDeviceCheck}>
                  {it.done ? '重测' : it.action}
                </button>
              ) : (
                <button className="btn btn--ghost btn--sm" onClick={onGoSetup}>
                  {it.done ? '修改' : it.action}
                </button>
              )}
            </li>
          )
        })}
      </ul>

      {/* 未完成时说明「为什么还不能开始」，而不是直接灰掉按钮 */}
      <div className="readiness-foot">
        <div className="readiness-why">
          {firstTodo ? (
            <>
              <IconKeyboard size={15} className="inline-ic" />
              还需完成「{firstTodo.title}」。也可以先用示例简历跑一遍，之后再补。
            </>
          ) : null}
        </div>
        <div className="row gap-8 row--wrap">
          <button
            className="btn btn--primary"
            onClick={firstTodo?.key === 'device' ? onGoDeviceCheck : onGoSetup}
          >
            {firstTodo?.key === 'device' ? (
              <>
                <IconMic size={16} /> 开始自检
              </>
            ) : (
              <>
                <IconPlay size={16} /> 继续准备
              </>
            )}
          </button>
          <button className="btn btn--ghost" onClick={onDemo} disabled={seeding}>
            {seeding ? <span className="spinner" /> : <IconUpload size={16} />}
            {seeding ? '正在载入示例…' : '一键体验示例简历'}
          </button>
        </div>
      </div>
    </section>
  )
}

import { useLocation, useNavigate } from 'react-router-dom'
import { IconDoc, IconLock } from './icons'

/**
 * 「仅登录用户可见」的页面占位（游客模式）
 *
 * 为什么要有它：游客模式下**所有数据不可见、不保存**（含面试记录）。因此
 * 「我的面试」（报告 / 错题本 / 录音回放）与「简历优化」这两块对游客没有意义——
 * 报告列表、错题、录音都不会存在，简历优化还会往服务端写东西。
 *
 * 处理方式是**渲染一张说明卡**，而不是弹一句 Toast 就跳走：
 *  · 用户能看到自己点进来的是什么功能、为什么现在不能用；
 *  · 保留 URL（可直接收藏/分享，登录后原地返回）；
 *  · 给出明确的下一步（登录 / 注册 / 回去先跑一场）。
 *
 * props
 *  - what: 功能名（如「我的面试」），用于文案
 *  - points: 该功能登录后能得到什么（字符串数组）
 */
export default function LoginRequired({ what = '这个功能', points = [] }) {
  const nav = useNavigate()
  const location = useLocation()

  return (
    <div className="container">
      <div className="card mt-16">
        <div className="empty">
          <IconLock size={30} style={{ color: 'var(--text-mute)' }} />
          <div className="empty-title">{what}仅登录用户可见</div>
          <div className="empty-desc">
            当前是<strong>游客模式</strong>：为了不让任何人的数据被别人看到，
            游客模式下所有数据都不保存、也不展示。
            <br />
            登录后你的简历、面试记录、报告与录音都只属于你自己。
          </div>

          {points.length > 0 && (
            <ul className="login-points">
              {points.map((p) => (
                <li key={p}>
                  <IconDoc size={14} /> {p}
                </li>
              ))}
            </ul>
          )}

          <div className="row gap-12 mt-16" style={{ justifyContent: 'center', flexWrap: 'wrap' }}>
            <button
              className="btn btn--primary"
              onClick={() => nav('/login', { state: { from: location.pathname + location.search } })}
            >
              登录 / 注册
            </button>
            <button className="btn btn--ghost" onClick={() => nav('/')}>
              返回首页
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}

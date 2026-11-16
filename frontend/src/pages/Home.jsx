import { useCallback, useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  IconArrowRight,
  IconBriefcase,
  IconBriefcaseUser,
  IconChart,
  IconCheck,
  IconDoc,
  IconFilePen,
  IconLock,
  IconMic,
  IconPlay,
  IconTrophy,
} from '../components/icons'
import {
  getInterviewState,
  getReportHistory,
  loadLocal,
  saveLocal,
} from '../api/client'
import { useAuth } from '../auth/AuthContext'
import './home.css'

/* ==========================================================================
   首页 = 功能入口页

   口径（按需求收敛过一次，别再往里加内容）：
     · **只放本项目自己的功能**：模块矩阵 + 我的面试报告 +（有未完成面试时的）继续入口。
     · 不放竞品对比、不放运行状态条、不放四步叙事与六维模型说明——
       那些是「介绍产品」而不是「使用功能」，占了首屏却点不动。
     · 功能列表是**严格的 N×M 网格**：桌面 3 列 × 2 行，平板 2 列 × 3 行，手机 1 列 × 6 行。
       卡片数刻意保持 6 张（把「六维评分报告」与「复盘闭环」合成一张「评分与复盘」），
       这样任何一档宽度都不会出现「最后一行只剩一张卡」的残缺排布。
   ========================================================================== */

/* 六个模块：顺序即导航顺序（头部导航与这里同源） */
const MODULES = [
  {
    title: '岗位信息汇总',
    tone: 'tone-blue',
    Icon: IconBriefcase,
    to: '/jobs',
    cta: '打开岗位库',
    desc: '先确定投什么岗：共享岗位库 + 你自己的投递进度与统计。',
    items: [
      { name: '共享岗位库', desc: '企业 / 雇主类型 / 行业 / 毕业届别 / 地点 / 专业 / 岗位 / 投递链接' },
      { name: '筛选与搜索', desc: '六个维度任意多选 + 全文搜索（⌘K），每页 30 条翻页' },
      { name: '投递进度', desc: '15 个状态，表格里点一下就能改；进度各人独立' },
      { name: '投递统计', desc: 'KPI、投递漏斗、环节淘汰、进度与雇主类型分布' },
      { name: '导入与导出', desc: '.xlsx / .csv / 粘贴导入岗位；自己的投递记录导出真 .xlsx' },
    ],
  },
  {
    title: '简历优化',
    tone: 'tone-violet',
    Icon: IconFilePen,
    to: '/resume-optimizer',
    login: true,
    cta: '去优化简历',
    desc: '把「投出去的那一版」改到机器和人都读得懂。',
    items: [
      { name: 'JD 关键词覆盖率', desc: '纯规则计算、不耗次数，缺哪些词一眼可见' },
      { name: 'AI STAR 润色', desc: '中 / 英文两种口径，逐句接受或回退，不覆盖你的表达' },
      { name: 'ATS 兼容性检测', desc: '五个模块打分 + 具体修改建议' },
      { name: '旧简历解析回填', desc: '拖入旧 PDF 自动结构化，不用从头手打' },
      { name: '导出 PDF', desc: '两套模板，自动适配一整页字号' },
    ],
  },
  {
    title: '数字人模拟面试',
    tone: 'tone-teal',
    Icon: IconBriefcaseUser,
    to: '/setup',
    login: true,
    cta: '开始模拟面试',
    desc: '按简历 × JD × 公司画像出题、追问，练的是「说出来」。',
    items: [
      { name: '2D 数字人面试官', desc: '呼吸 / 点头 / 眨眼 / 口型随音量开合，字幕逐字同步' },
      { name: '语音与文字双模式', desc: '麦克风作答或随时切文字；本机录音可回放' },
      { name: '多环节出题与追问', desc: '自我介绍 → 项目深挖 → 专业考察 → 压力问题' },
      { name: '女 / 男音色可选', desc: 'TTS 走浏览器原生或阿里云，零密钥也能出声' },
      { name: '设备自检', desc: '麦克风 / 语音 / 识别三项一次测完' },
      { name: '中断可续', desc: '中途退出不结束会话，回来接着答' },
    ],
  },
  {
    title: '评分与复盘',
    tone: 'tone-blue',
    Icon: IconChart,
    to: '/my-interviews',
    login: true,
    cta: '看报告与错题本',
    desc: '每个分数都要有依据，每条建议都要能执行。',
    items: [
      { name: '六维结构化评分', desc: '专业 / 逻辑 / 表达 / 匹配 / 应变 / 文化，含权重与考察依据' },
      { name: '逐题分析与基准', desc: '每题的要点、缺口与更好的说法；同岗位 P25/P50/P75' },
      { name: '7 天提升计划', desc: '可勾选的每日任务，把「知道差在哪」变成「明天做什么」' },
      { name: '错题本', desc: '低于 6 分的题自动收集，可一键发起同环节 3 题微面试' },
      { name: '录音与曲线', desc: '本机录音回放 + 最近 10 场评分趋势' },
    ],
  },
  {
    title: '成长与榜单',
    tone: 'tone-teal',
    Icon: IconTrophy,
    to: '/leaderboard',
    // 排行榜是**公开**页面（导航里对游客也不带锁）：它不产生也不暴露个人数据。
    public: true,
    cta: '看看排行榜',
    desc: '把「坚持」和「进步」也变成看得见的数据。',
    items: [
      { name: '成长面板', desc: '连续打卡、近 7 天格子、近 4 周汇总、维度首末对比' },
      { name: '三类排行榜', desc: '岗位榜 / 校园榜 / 进步榜，公开可见（不含隐私数据）' },
    ],
  },
  {
    title: '账号与数据私有',
    tone: 'tone-violet',
    Icon: IconLock,
    cta: '',
    desc: '求职数据是最私人的一类数据：能被谁看到，必须说清楚。',
    items: [
      { name: '登录才保存', desc: '简历、面试记录、报告、投递进度只属于你自己' },
      { name: '归属校验在服务端', desc: '每个接口都按令牌判定归属，别人的资源一律返回「不存在」' },
      { name: '游客模式', desc: '不登录只能看首页、排行榜与公开公司信息，且不保存任何数据' },
    ],
  },
]

export default function Home() {
  const nav = useNavigate()
  const { isGuest, user, loading: authLoading } = useAuth()
  /* ---- 未完成面试的恢复入口 ----
     退出面试时刻意**不**结束会话，因此这里要给出真实可点的回路，
     否则「可稍后继续」只是一句空话。 */
  const [resumable, setResumable] = useState(null)

  useEffect(() => {
    // 游客没有"未完成的面试"这回事（面试仅登录用户可用），也不该为此发一个必然 401 的请求
    if (authLoading || isGuest) {
      setResumable(null)
      return undefined
    }
    const sid = loadLocal('aic_last_session', '')
    if (!sid) return undefined
    let alive = true
    getInterviewState(sid)
      .then((st) => {
        if (!alive) return
        if (st?.status === 'ongoing') {
          setResumable({
            sessionId: sid,
            index: Number(st.question_index) || 0,
            total: Number(st.total_questions) || 0,
            jobTitle: st.job_title || '',
          })
        } else {
          // 已结束/已收敛的会话不再提示，顺手清掉本地指针
          saveLocal('aic_last_session', '')
          setResumable(null)
        }
      })
      .catch(() => {
        if (alive) setResumable(null)
      })
    return () => {
      alive = false
    }
  }, [authLoading, isGuest])

  /* ---- 历史报告（报告的唯一常驻入口）----
     报告按场次生成，入口不只是「答完最后一题自动跳转」；离开报告页后必须还能回来。 */
  const [reports, setReports] = useState([])
  const [reportsLoading, setReportsLoading] = useState(true)

  const loadReports = useCallback(async () => {
    // 游客模式下不存在「我的报告」：数据全部不保存（后端游客请求走进程内存库），
    // 因此连请求都不发，列表也不渲染。
    if (isGuest) {
      setReports([])
      setReportsLoading(false)
      return
    }
    setReportsLoading(true)
    try {
      const data = await getReportHistory()
      const items = Array.isArray(data?.items) ? data.items : []
      // 后端按时间升序返回，首页要「最近的在最前」
      setReports([...items].reverse())
    } catch {
      // 拉不到列表不影响首页其它内容，也不弹 Toast：静默降级为空列表
      setReports([])
    } finally {
      setReportsLoading(false)
    }
  }, [isGuest])

  useEffect(() => {
    loadReports()
  }, [loadReports])

  /** 需要登录的动作与入口：游客先去登录页，登录后原地回到目标路径 */
  const goOrLogin = (path) => {
    if (isGuest) {
      nav('/login', { state: { from: path } })
      return
    }
    nav(path)
  }

  return (
    <div className="container">
      {/* ---------------- 身份状态条（游客 / 已登录）----------------
          与「数据私有性」直接相关的一条事实说明，不是营销位。 */}
      {!authLoading && isGuest && (
        <div className="alert alert--warn guest-banner" role="status">
          <span aria-hidden="true">!</span>
          <span className="grow">
            当前是<strong>游客模式</strong>：可以浏览首页、排行榜与公开的公司信息，
            但<strong>模拟面试、简历优化、岗位信息汇总、我的面试都需要登录</strong>。
            游客的请求走进程内内存库，<strong>不保存任何数据</strong>，也看不到别人的数据。
          </span>
          <button className="btn btn--primary btn--sm" onClick={() => nav('/login')}>
            登录 / 注册
          </button>
        </div>
      )}
      {!authLoading && !isGuest && (
        <div className="alert alert--ok guest-banner" role="status">
          <span aria-hidden="true">✓</span>
          <span className="grow">
            已登录为 <strong>{user?.nickname || user?.username}</strong>，你的简历、面试记录、报告与投递进度仅自己可见。
          </span>
        </div>
      )}

      {/* ---------------- 页头：标题 + 一句定位 ----------------
          刻意不放任何大按钮：首页的入口就是下面那六张模块卡，
          页头只负责回答「这是什么」。 */}
      <header className="home-head">
        <h1 className="home-title">
          校招<span className="grad-text">全流程助手</span>
        </h1>
        <p className="home-sub">
          一个账号跑完求职季真正要做的四步：<strong>找岗投递 → 改简历 → 练面试 → 复盘改进</strong>。
          下面六个模块覆盖全部功能，点进去就能用。
        </p>
      </header>

      {/* ---------------- 未完成面试：继续入口 ---------------- */}
      {resumable && (
        <div className="alert alert--info mb-12" role="status">
          <IconPlay size={17} />
          <span className="grow">
            你有一场未完成的面试
            {resumable.jobTitle ? `（${resumable.jobTitle}）` : ''}
            {resumable.total
              ? `，进度 ${resumable.index || 0}/${resumable.total} 题`
              : ''}
            。会话已保留，随时可以接着答，报告也能随时生成。
          </span>
          <button className="btn btn--primary btn--sm" onClick={() => nav(`/interview/${resumable.sessionId}`)}>
            <IconPlay size={15} /> 继续面试
          </button>
          <button
            className="btn link-btn"
            onClick={() => nav(`/report/${resumable.sessionId}`)}
            title="直接基于已作答内容生成报告"
          >
            直接看报告
          </button>
        </div>
      )}

      {/* ---------------- 功能矩阵：严格 3×2（六大块） ---------------- */}
      <section className="sec-block">
        <div className="sec-head">
          <h2 className="page-title">全部功能</h2>
          <p className="page-sub">
            六个模块的完整能力都在这里，没有藏在二级页面里的「神秘功能」。
          </p>
        </div>
        <div className="feat-grid">
          {MODULES.map((m) => (
            <section className="card feat-card" key={m.title}>
              <div className="feat-head">
                <span className={`module-icon ${m.tone}`}>
                  <m.Icon size={22} />
                </span>
                <div>
                  <div className="feat-title">{m.title}</div>
                  <p className="feat-desc">{m.desc}</p>
                </div>
              </div>
              <ul className="feat-items">
                {m.items.map((it) => (
                  <li key={it.name}>
                    <IconCheck size={14} className="feat-tick" />
                    <span>
                      <strong>{it.name}</strong>
                      <span className="feat-item-desc">{it.desc}</span>
                    </span>
                  </li>
                ))}
              </ul>
              {m.to ? (
                <button
                  className="btn btn--soft btn--sm feat-cta"
                  onClick={() => (m.public ? nav(m.to) : goOrLogin(m.to))}
                >
                  {m.cta} <IconArrowRight size={15} />
                </button>
              ) : (
                <span className="feat-cta-note">
                  <IconLock size={14} className="inline-ic" />
                  登录后你的数据只有你自己能看到
                </span>
              )}
            </section>
          ))}
        </div>
      </section>

      {/* ---------------- 我的面试报告 ----------------
          **游客模式不渲染**：游客的数据不保存，展示一个空列表只会让人误以为"记录丢了"。 */}
      {!authLoading && !isGuest && (
        <section className="sec-block home-reports">
          <div className="sec-head">
            <h2 className="page-title">我的面试报告</h2>
            <p className="page-sub">
              {reports.length
                ? `最近 ${reports.length} 场已完成面试，点开可回看六维评分与改进计划`
                : '每跑完一场模拟面试，都会生成一份可回看的六维评分报告'}
            </p>
          </div>

          {reportsLoading ? (
            <div className="card card--flat">
              <div className="skeleton sk-line sk-line--60" />
              <div className="skeleton sk-line" style={{ marginTop: 10 }} />
              <div className="skeleton sk-line sk-line--40" style={{ marginTop: 10 }} />
            </div>
          ) : reports.length === 0 ? (
            <div className="card card--flat">
              <div className="empty">
                <div className="empty-title">还没有已完成的面试</div>
                <div className="empty-desc">
                  完成一场模拟面试后，报告会出现在这里，随时可以回看或重新生成。
                </div>
                <button className="btn btn--primary mt-16" onClick={() => goOrLogin('/setup')}>
                  <IconPlay size={16} /> 开始一场模拟面试
                </button>
              </div>
            </div>
          ) : (
            <ul className="report-list">
              {reports.map((r, i) => (
                <li key={r.session_id} className="report-item">
                  <span className="report-date mono">{r.date || '—'}</span>
                  <span className="report-job">{r.job_title || '目标岗位'}</span>
                  {i === 0 && <span className="chip chip--accent">最近</span>}
                  <span className="report-score mono">
                    {Number(r.score) > 0 ? `${Number(r.score).toFixed(1)} 分` : '—'}
                  </span>
                  <button
                    className="btn btn--ghost btn--sm"
                    onClick={() => nav(`/report/${r.session_id}`)}
                    aria-label={`查看 ${r.date || ''} ${r.job_title || '目标岗位'} 的面试报告`}
                  >
                    <IconDoc size={15} /> 查看报告
                  </button>
                </li>
              ))}
            </ul>
          )}

          {!reportsLoading && reports.length > 0 && (
            <p className="mute-2 mt-12" style={{ fontSize: 12 }}>
              只列出已经生成过报告的场次（最近 10 场）；只有「已结束」的面试会进入这份列表。
            </p>
          )}
        </section>
      )}

      {/* 页脚只留一句诚实声明（不是运行状态条，也不是营销） */}
      <footer className="home-foot">
        <span className="mute-2" style={{ fontSize: 12 }}>
          校招全流程助手 · 面试结果与评分仅供练习参考
        </span>
      </footer>
    </div>
  )
}

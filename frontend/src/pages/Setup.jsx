import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import SectionCard from '../components/SectionCard'
import ScoreRing from '../components/ScoreRing'
import Avatar from '../components/Avatar'
import DeviceCheck from '../components/DeviceCheck'
import LoginRequired from '../components/LoginRequired'
import { useAuth } from '../auth/AuthContext'
import {
  IconCheckCircle,
  IconCompass,
  IconInfo,
  IconLock,
  IconMic,
  IconPlay,
  IconUpload,
  IconWarn,
} from '../components/icons'
import {
  computeMatch,
  demoSeed,
  getCompany,
  getHealth,
  getJd,
  getMetaOptions,
  getResume,
  interviewStart,
  listJds,
  loadLocal,
  manualCompany,
  notify,
  parseJd,
  parseResumeText,
  saveLocal,
  searchCompany,
  updateJd,
  updateResume,
  uploadJd,
  uploadResume,
} from '../api/client'
import './setup.css'

/* ==========================================================================
   工具
   ========================================================================== */
const MAX_MB = 5

const arrToText = (arr) => (Array.isArray(arr) ? arr.join('\n') : '')
const textToArr = (t) =>
  String(t || '')
    .split('\n')
    .map((s) => s.trim())
    .filter(Boolean)

const emptyResume = () => ({
  basic: { name: '', school: '', major: '', degree: '', grade: '', phone: '', email: '' },
  education: [],
  internships: [],
  projects: [],
  campus_experience: [],
  skills: [],
  awards: [],
  self_evaluation: '',
})

const emptyJd = () => ({
  basic: { title: '', department: '', location: '' },
  responsibilities: [],
  requirements: { degree: '', experience: '', hard_skills: [], soft_skills: [], bonus: [] },
  keywords: [],
})

function normResume(parsed) {
  const src = parsed || {}
  return {
    basic: {
      name: '',
      school: '',
      major: '',
      degree: '',
      grade: '',
      phone: '',
      email: '',
      ...(src.basic || {}),
    },
    education: Array.isArray(src.education) ? src.education : [],
    internships: (Array.isArray(src.internships) ? src.internships : []).map((i) => ({
      company: '',
      title: '',
      start: '',
      end: '',
      duties: [],
      achievements: [],
      ...(i || {}),
    })),
    projects: (Array.isArray(src.projects) ? src.projects : []).map((p) => ({
      name: '',
      role: '',
      start: '',
      end: '',
      description: '',
      tech_stack: [],
      ...(p || {}),
    })),
    campus_experience: (Array.isArray(src.campus_experience) ? src.campus_experience : []).map((c) => ({
      name: '',
      role: '',
      start: '',
      end: '',
      description: '',
      highlights: [],
      ...(c || {}),
    })),
    skills: Array.isArray(src.skills) ? [...src.skills] : [],
    awards: Array.isArray(src.awards) ? [...src.awards] : [],
    self_evaluation: src.self_evaluation || '',
  }
}

function normJd(parsed) {
  const src = parsed || {}
  const req = src.requirements || {}
  return {
    basic: { title: '', department: '', location: '', ...(src.basic || {}) },
    responsibilities: Array.isArray(src.responsibilities) ? [...src.responsibilities] : [],
    requirements: {
      degree: req.degree || '',
      experience: req.experience || '',
      hard_skills: Array.isArray(req.hard_skills) ? [...req.hard_skills] : [],
      soft_skills: Array.isArray(req.soft_skills) ? [...req.soft_skills] : [],
      bonus: Array.isArray(req.bonus) ? [...req.bonus] : [],
    },
    keywords: Array.isArray(src.keywords) ? [...src.keywords] : [],
  }
}

/* ==========================================================================
   通用子组件
   ========================================================================== */
function Modal({ open, title, desc, onClose, children, footer, wide = false, loading = false }) {
  if (!open) return null
  return (
    <div className="modal-mask" role="dialog" aria-modal="true">
      <div className={`modal ${wide ? 'modal--wide' : ''}`}>
        <div className="modal-head">
          <div>
            <div className="card-title">{title}</div>
            {desc && <div className="sec-desc">{desc}</div>}
          </div>
          <button className="btn btn--ghost btn--sm" onClick={onClose} disabled={loading}>
            关闭
          </button>
        </div>
        <div className="modal-body">{children}</div>
        {footer && <div className="modal-foot">{footer}</div>}
      </div>
    </div>
  )
}

function TagInput({ value = [], onChange, placeholder = '输入后回车添加' }) {
  const [text, setText] = useState('')
  const add = () => {
    const v = text.trim()
    if (!v) return
    if (!value.includes(v)) onChange([...value, v])
    setText('')
  }
  return (
    <div>
      <div className="tag-input">
        <input
          className="input"
          value={text}
          placeholder={placeholder}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter') {
              e.preventDefault()
              add()
            }
          }}
        />
        <button type="button" className="btn btn--soft" onClick={add}>
          添加
        </button>
      </div>
      {value.length > 0 && (
        <div className="tag-list mt-8">
          {value.map((t, i) => (
            <span className="tag-edit" key={`${t}-${i}`}>
              {t}
              <button
                type="button"
                onClick={() => onChange(value.filter((_, idx) => idx !== i))}
                aria-label={`删除 ${t}`}
              >
                ×
              </button>
            </span>
          ))}
        </div>
      )}
    </div>
  )
}

function SkeletonBlock({ lines = 3 }) {
  return (
    <div>
      {Array.from({ length: lines }).map((_, i) => (
        <div key={i} className={`skeleton sk-line ${i % 2 ? 'sk-line--60' : ''}`} />
      ))}
    </div>
  )
}

/* ==========================================================================
   页面
   ========================================================================== */
export default function Setup() {
  const nav = useNavigate()
  const [params] = useSearchParams()
  // 模拟面试仅登录用户可用：游客进来看到说明卡，后端接口同样返回 401
  const { isGuest, loading: authLoading } = useAuth()
  /* ---- 档案状态 ---- */
  const [resume, setResume] = useState(null) // ResumeUploadResponse
  const [resumeLoading, setResumeLoading] = useState(false)
  const [resumeEditOpen, setResumeEditOpen] = useState(false)
  const [resumeSaving, setResumeSaving] = useState(false)
  const [pasteOpen, setPasteOpen] = useState(false)
  const [pasteText, setPasteText] = useState('')

  const [jd, setJd] = useState(null) // JDParseResponse
  const [jdLoading, setJdLoading] = useState(false)
  const [jdText, setJdText] = useState('')
  const [jdTitle, setJdTitle] = useState('')
  const [jdEditOpen, setJdEditOpen] = useState(false)
  const [jdSaving, setJdSaving] = useState(false)

  const [companyName, setCompanyName] = useState('')
  const [companyId, setCompanyId] = useState('')
  const [companyProfile, setCompanyProfile] = useState(null)
  const [companyQuery, setCompanyQuery] = useState('')
  const [companyHits, setCompanyHits] = useState([])
  const [companySearching, setCompanySearching] = useState(false)
  const [companyManualOpen, setCompanyManualOpen] = useState(false)
  const [companySaving, setCompanySaving] = useState(false)
  const [companyManualDraft, setCompanyManualDraft] = useState({
    name: '',
    industry: '',
    scale: '',
    business: '',
    culture_tags: [],
    interview_style: '结构化面',
    style_desc: '',
  })

  const [match, setMatch] = useState(null)
  const [matchLoading, setMatchLoading] = useState(false)
  const [matchError, setMatchError] = useState('')
  /* ---- 体验增强 §8：维度下钻 / 趋势标记 / 多 JD 对比 ---- */
  const [openDim, setOpenDim] = useState('')
  const [matchTrend, setMatchTrend] = useState(null)
  const [compareList, setCompareList] = useState([])
  const [compareLoading, setCompareLoading] = useState(false)

  /* ---- 面试设置 ---- */
  const [meta, setMeta] = useState(null)
  const [interviewer, setInterviewer] = useState('female')
  const [mode, setMode] = useState('voice')
  const [questionCount, setQuestionCount] = useState(9)
  const [starting, setStarting] = useState(false)
  /* 体验增强 §14：压力面试模式（练习模式，不改变评分算法，只改变节奏与题型偏好） */
  const [pressureMode, setPressureMode] = useState(false)

  /* ---- 设备自检（体验增强 §3）：进入面试前的最后一道防线 ---- */
  const [devOpen, setDevOpen] = useState(false)
  const [deviceChecked, setDeviceChecked] = useState(() => loadLocal('aic_device_checked', false) === true)
  const [voice, setVoice] = useState({ tts: '', asr: '' })

  const asrOK = typeof window !== 'undefined' && !!(window.SpeechRecognition || window.webkitSpeechRecognition)
  const matchKeyRef = useRef('')
  const companySeqRef = useRef(0)

  /* ---- 元信息 + 浏览器能力探测 ---- */
  useEffect(() => {
    getMetaOptions()
      .then(setMeta)
      .catch(() => setMeta(null))
    // 实际生效的语音提供方：自检结果必须与后端真实链路一致
    getHealth()
      .then((h) => setVoice({ tts: h?.tts_provider || '', asr: h?.asr_provider || '' }))
      .catch(() => setVoice({ tts: '', asr: '' }))
    if (!asrOK) {
      setMode('text')
      notify('当前浏览器不支持语音识别，已默认使用「文字回答」模式', 'warn', 3600)
    }
  }, [asrOK])

  /* ---- 一键示例：从 URL 恢复 ids ---- */
  useEffect(() => {
    const rid = params.get('resume_id')
    const jid = params.get('jd_id')
    const cid = params.get('company_id')
    if (rid) {
      setResumeLoading(true)
      getResume(rid)
        .then(setResume)
        .catch(() => notify('示例简历加载失败，可手动上传', 'warn'))
        .finally(() => setResumeLoading(false))
    }
    if (jid) {
      setJdLoading(true)
      getJd(jid)
        .then(setJd)
        .catch(() => notify('示例 JD 加载失败，可粘贴岗位描述', 'warn'))
        .finally(() => setJdLoading(false))
    }
    if (cid) {
      setCompanyId(cid)
      getCompany(cid)
        .then((data) => {
          setCompanyProfile(data?.profile || null)
          setCompanyName(data?.profile?.name || '')
        })
        .catch(() => {
          /* 示例公司画像读取失败不阻断流程 */
        })
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  /* ---- 简历：上传 ---- */
  const doUploadResume = useCallback(async (file) => {
    if (!file) return
    if (!/\.pdf$/i.test(file.name || '')) {
      notify('仅支持 PDF 文件（.pdf），请重新选择', 'error')
      return
    }
    if (file.size > MAX_MB * 1024 * 1024) {
      notify(`文件 ${(file.size / 1024 / 1024).toFixed(1)}MB 超过上限 ${MAX_MB}MB`, 'error')
      return
    }
    setResumeLoading(true)
    try {
      const data = await uploadResume(file)
      setResume(data)
      setResumeEditOpen(true)
      notify('简历解析完成，请确认信息是否准确', 'success')
    } catch {
      /* 已由 client 弹 Toast */
    } finally {
      setResumeLoading(false)
    }
  }, [])

  /* ---- 简历：用示例填充 ---- */
  const useDemoResume = async () => {
    setResumeLoading(true)
    try {
      const data = await demoSeed()
      setResume(data?.resume || null)
      notify('已载入示例简历，可直接编辑成你自己的经历', 'success')
    } catch {
      notify('示例简历载入失败，请上传 PDF', 'warn')
    } finally {
      setResumeLoading(false)
    }
  }

  /* ---- 简历：粘贴文本解析（与 PDF 同一套结构化解析） ---- */
  const startManualResume = async () => {
    const text = pasteText.trim()
    if (text.length < 20) {
      notify('请先粘贴完整简历文本（至少 20 个字）', 'warn')
      return
    }
    setResumeLoading(true)
    try {
      const data = await parseResumeText(text)
      setResume(data)
      // 解析结果一律给「确认/编辑」入口（文档 §3.3.1 设计要点）
      setResumeEditOpen(true)
      notify('简历文本解析完成，请核对并可编辑', 'success')
    } catch {
      /* 已弹 Toast */
    } finally {
      setResumeLoading(false)
    }
  }

  /* ---- 简历：保存（PUT /api/resume/{id}） ---- */
  const saveResume = async (draft) => {
    if (!resume?.resume_id) return
    setResumeSaving(true)
    try {
      const data = await updateResume(resume.resume_id, draft)
      setResume(data)
      setResumeEditOpen(false)
      notify('简历档案已保存', 'success')
    } catch {
      /* 已弹 Toast */
    } finally {
      setResumeSaving(false)
    }
  }

  /* ---- JD：粘贴文本解析 ---- */
  const doParseJd = async () => {
    const text = jdText.trim()
    if (text.length < 20) {
      notify('岗位描述太短了，请粘贴完整 JD（至少 20 字）', 'warn')
      return
    }
    setJdLoading(true)
    try {
      const data = await parseJd(text, jdTitle.trim())
      setJd(data)
      notify('JD 解析完成，请确认岗位信息', 'success')
    } catch {
      /* 已弹 Toast */
    } finally {
      setJdLoading(false)
    }
  }

  const doUploadJd = async (file) => {
    if (!file) return
    if (!/\.pdf$/i.test(file.name || '')) {
      notify('仅支持 PDF 文件（.pdf）', 'error')
      return
    }
    if (file.size > MAX_MB * 1024 * 1024) {
      notify(`文件超过 ${MAX_MB}MB 上限`, 'error')
      return
    }
    setJdLoading(true)
    try {
      const data = await uploadJd(file)
      setJd(data)
      notify('JD 解析完成', 'success')
    } catch {
      /* 已弹 Toast */
    } finally {
      setJdLoading(false)
    }
  }

  const saveJd = async (draft) => {
    if (!jd?.jd_id) return
    setJdSaving(true)
    try {
      const data = await updateJd(jd.jd_id, draft)
      setJd(data)
      setJdEditOpen(false)
      notify('岗位信息已保存', 'success')
    } catch {
      /* 已弹 Toast */
    } finally {
      setJdSaving(false)
    }
  }

  /* ---- 公司：实时搜索（防抖） ---- */
  useEffect(() => {
    const q = companyQuery.trim()
    if (q.length < 1) {
      setCompanyHits([])
      return undefined
    }
    setCompanySearching(true)
    // 只 clearTimeout 不足以避免乱序：已经发出的请求无法取消，
    // 上一个关键词的慢响应会覆盖新关键词的结果。用序号只接受最新一次。
    const seq = (companySeqRef.current += 1)
    const t = setTimeout(() => {
      searchCompany(q, 8)
        .then((data) => {
          if (seq !== companySeqRef.current) return
          setCompanyHits(data?.items || [])
        })
        .catch(() => {
          if (seq !== companySeqRef.current) return
          setCompanyHits([])
        })
        .finally(() => {
          if (seq === companySeqRef.current) setCompanySearching(false)
        })
    }, 320)
    return () => clearTimeout(t)
  }, [companyQuery])

  /* 选中搜索结果：取内置画像 → 落库拿 DB 版 company_id（供 /match 与 /interview/start 使用） */
  const pickCompany = async (item) => {
    setCompanySearching(true)
    setCompanyQuery('')
    setCompanyHits([])
    setCompanyName(item?.name || '')
    try {
      let profile = null
      try {
        const detail = await getCompany(item.company_id)
        profile = detail?.profile || null
      } catch {
        profile = null
      }
      if (profile) {
        setCompanyProfile(profile)
        const saved = await manualCompany({
          name: profile.name || item.name,
          industry: profile.industry || '',
          scale: profile.scale || '',
          business: profile.business || '',
          culture_tags: profile.culture_tags || [],
          interview_style: profile.interview_style || '结构化面',
          style_desc: profile.style_desc || '',
        })
        setCompanyId(saved?.company_id || '')
        setCompanyProfile(saved?.profile || profile)
        notify(`已识别「${profile.name}」公司画像（${profile.interview_style || '结构化面'}）`, 'success')
      } else {
        openManual(item.name)
      }
    } catch {
      notify('公司画像获取失败，可手动填写', 'warn')
      openManual(item.name)
    } finally {
      setCompanySearching(false)
    }
  }

  const openManual = (name = '') => {
    setCompanyManualDraft((prev) => ({ ...prev, name: name || prev.name || companyName || '' }))
    setCompanyManualOpen(true)
  }

  const saveManualCompany = async () => {
    const name = (companyManualDraft.name || '').trim()
    if (!name) {
      notify('请填写公司名称', 'warn')
      return
    }
    setCompanySaving(true)
    try {
      const data = await manualCompany({ ...companyManualDraft, name })
      setCompanyId(data?.company_id || '')
      setCompanyProfile(data?.profile || null)
      setCompanyName(data?.profile?.name || name)
      setCompanyManualOpen(false)
      notify('公司信息已保存', 'success')
    } catch {
      /* 已弹 Toast */
    } finally {
      setCompanySaving(false)
    }
  }

  /* ---- 匹配度：简历 + JD 就绪后自动计算 ---- */
  const runMatch = useCallback(
    async (force = false) => {
      if (!resume?.resume_id || !jd?.jd_id) return
      const key = `${resume.resume_id}|${jd.jd_id}|${companyId}`
      if (!force && key === matchKeyRef.current) return
      matchKeyRef.current = key
      setMatchLoading(true)
      setMatchError('')
      try {
        const payload = { resume_id: resume.resume_id, jd_id: jd.jd_id }
        if (companyId) payload.company_id = companyId
        const data = await computeMatch(payload)
        setMatch(data)
        // 体验增强 §8：趋势标记——与上一次**同一份简历+同一个岗位**的结果比较。
        // 只报告"哪一维动了多少"，不编造原因（前端无从知道简历具体改了什么）。
        try {
          const storeKey = `aic_match_${resume.resume_id}_${jd.jd_id}`
          const prev = loadLocal(storeKey, null)
          if (prev && Number.isFinite(Number(prev.overall))) {
            const delta = Number(data?.overall || 0) - Number(prev.overall)
            let top = null
            const prevDims = {}
            for (const b of prev.breakdown || []) prevDims[b.key] = Number(b.score) || 0
            for (const b of data?.breakdown || []) {
              const before = prevDims[b.key] === undefined ? Number(b.score) || 0 : prevDims[b.key]
              const d = (Number(b.score) || 0) - before
              if (!top || Math.abs(d) > Math.abs(top.delta)) top = { name: b.dimension, delta: d }
            }
            setMatchTrend({ delta, top, at: prev.at || '' })
          } else {
            setMatchTrend(null)
          }
          saveLocal(storeKey, {
            overall: data?.overall || 0,
            breakdown: (data?.breakdown || []).map((b) => ({ key: b.key, score: b.score })),
            at: new Date().toISOString().slice(0, 10),
          })
        } catch {
          setMatchTrend(null)
        }
      } catch (err) {
        setMatch(null)
        setMatchError(err?.message || '匹配度计算失败')
      } finally {
        setMatchLoading(false)
      }
    },
    [resume, jd, companyId]
  )

  useEffect(() => {
    runMatch(false)
  }, [runMatch])

  /* ---- 体验增强 §8：同一份简历挂多个 JD 做对比 ---- */
  const loadCompare = useCallback(async () => {
    if (!resume?.resume_id || !jd?.jd_id) return
    setCompareLoading(true)
    try {
      const list = await listJds(8)
      // 当前 JD 一定在列，再取最多 2 个"其它岗位"凑成 3 列对比
      const others = (list?.items || []).filter((it) => it.jd_id !== jd.jd_id).slice(0, 2)
      const targets = [jd.jd_id, ...others.map((o) => o.jd_id)]
      const results = await Promise.all(
        targets.map(async (id) => {
          const meta = (list?.items || []).find((it) => it.jd_id === id) || {}
          try {
            const payload = { resume_id: resume.resume_id, jd_id: id }
            if (companyId) payload.company_id = companyId
            const data = await computeMatch(payload)
            return {
              jdId: id,
              title: meta.title || (id === jd.jd_id ? jd.parsed?.basic?.title : '') || '未命名岗位',
              department: meta.department || '',
              overall: Number(data?.overall) || 0,
              breakdown: data?.breakdown || [],
              isCurrent: id === jd.jd_id,
            }
          } catch {
            return null
          }
        })
      )
      setCompareList(results.filter(Boolean))
    } catch {
      setCompareList([])
    } finally {
      setCompareLoading(false)
    }
  }, [resume, jd, companyId])

  useEffect(() => {
    loadCompare()
  }, [loadCompare])

  /** 切到另一个岗位：重新取该 JD 的完整解析结果并设为当前目标岗位 */
  const switchJd = async (jdId) => {
    if (!jdId || jdId === jd?.jd_id) return
    try {
      const data = await getJd(jdId)
      setJd(data)
      notify('已切换目标岗位，匹配度会重新计算', 'success', 2600)
    } catch {
      /* client 已弹 Toast */
    }
  }

  /* ---- 开始面试 ---- */
  // 解析失败的档案（例如扫描件 PDF 提不出文本）不能开始面试：
  // 后端此时 parsed_json 为空，出题与评分都会没有依据。
  const resumeReady = !!resume?.resume_id && resume?.parse_status !== 'failed'
  const jdReady = !!jd?.jd_id && jd?.parse_status !== 'failed'
  const canStart = resumeReady && jdReady
  const onStart = async () => {
    if (!canStart) {
      if (resume?.parse_status === 'failed' || jd?.parse_status === 'failed') {
        notify('档案解析失败：请改用「粘贴文本」方式重新导入后再开始', 'warn', 3600)
      } else {
        notify('请先完成「简历」和「岗位 JD」两区', 'warn')
      }
      return
    }
    setStarting(true)
    try {
      const payload = {
        resume_id: resume.resume_id,
        jd_id: jd.jd_id,
        mode,
        interviewer,
        question_count: questionCount,
      }
      if (pressureMode) payload.pressure_mode = true
      if (companyId) payload.company_id = companyId
      const data = await interviewStart(payload)
      if (!data?.session_id) throw new Error('会话创建失败')
      // 记住本场会话：退出后首页据此提供「继续未完成的面试」入口（体验增强 §2）
      saveLocal('aic_last_session', data.session_id)
      nav(`/interview/${data.session_id}`, { state: { start: data } })
    } catch {
      /* 已弹 Toast */
    } finally {
      setStarting(false)
    }
  }

  const interviewerOptions = useMemo(
    () =>
      meta?.interviewer_options?.length
        ? meta.interviewer_options
        : [
            { key: 'female', label: '知性女面试官', style: '温和专业，擅长循循善诱' },
            { key: 'male', label: '沉稳男面试官', style: '沉稳严谨，擅长层层追问' },
          ],
    [meta]
  )

  const resumeBadge = resume?.parse_status === 'failed' ? '解析失败' : resume?.parse_source === 'llm' ? '大模型解析' : '规则引擎解析'
  const jdBadge = jd?.parse_status === 'failed' ? '解析失败' : jd?.parse_source === 'llm' ? '大模型解析' : '规则引擎解析'
  const resumeBadgeClass = resume?.parse_status === 'failed' ? 'chip--danger' : 'chip--ok'
  const jdBadgeClass = jd?.parse_status === 'failed' ? 'chip--danger' : 'chip--ok'

  /* 经历概览：不打开编辑弹窗也能核对解析结果（含校园经历），
     三类经历用同一套渲染，避免只显示某一类造成「是不是漏解析了」的困惑 */
  const resumeSections = resume
    ? [
        {
          label: '实习经历',
          items: (resume.parsed?.internships || []).map((it) => ({
            head: [it.company, it.title].filter(Boolean).join(' · '),
            span: [it.start, it.end].filter(Boolean).join('-'),
            extra: (it.achievements || [])[0] || (it.duties || [])[0] || '',
          })),
        },
        {
          label: '项目经历',
          items: (resume.parsed?.projects || []).map((it) => ({
            head: [it.name, it.role].filter(Boolean).join(' · '),
            span: [it.start, it.end].filter(Boolean).join('-'),
            extra: it.description || '',
          })),
        },
        {
          label: '校园经历',
          items: (resume.parsed?.campus_experience || []).map((it) => ({
            head: [it.name, it.role].filter(Boolean).join(' · '),
            span: [it.start, it.end].filter(Boolean).join('-'),
            extra: (it.highlights || [])[0] || it.description || '',
          })),
        },
      ].filter((sec) => sec.items.length > 0)
    : []

  // 游客模式：模拟面试仅登录用户可用。这一层是体验上的告知，
  // 后端 `require_login` 依赖同样会拒（401），两边一致。
  if (!authLoading && isGuest) {
    return (
      <LoginRequired
        what="模拟面试"
        points={[
          '数字人面试官语音对话，完整走完 8-10 题',
          '六维结构化评分报告与逐题依据',
          '答题记录、错题本与录音回放（录音只存本机）',
        ]}
      />
    )
  }

  return (
    <div className="container">
      <div className="page-head">
        <div>
          <h1 className="page-title">准备你的面试</h1>
          <p className="page-sub">三份档案越完整，面试官的问题就越贴着你的经历走</p>
        </div>
        <div className="row gap-8">
          <button className="btn btn--ghost btn--sm" onClick={() => nav('/')}>
            返回首页
          </button>
        </div>
      </div>

      <div className="setup-grid">
        {/* ================= ① 简历 ================= */}
        <SectionCard
          index="①"
          title="简历档案"
          desc="上传 PDF 简历自动解析，或手动填写（支持修改）"
          right={
            resume ? (
              <span className={`chip ${resumeBadgeClass}`}>{resumeBadge}</span>
            ) : (
              <span className="chip">未上传</span>
            )
          }
        >
          {resumeLoading ? (
            <SkeletonBlock lines={4} />
          ) : resume ? (
            <div>
              <div className="parse-summary">
                <div className="row row--between">
                  <span className="chip chip--primary">{resume.file_name || '简历'}</span>
                  <span className={`chip ${resumeBadgeClass}`}>{resumeBadge}</span>
                </div>
                <div className="summary-line">{resume.summary_line || '已解析简历档案'}</div>
                <div className="kv-grid">
                  {[
                    ['姓名', resume.parsed?.basic?.name],
                    ['学校', resume.parsed?.basic?.school],
                    ['专业', resume.parsed?.basic?.major],
                    ['学历', resume.parsed?.basic?.degree],
                    ['年级', resume.parsed?.basic?.grade],
                  ]
                    .filter(([, v]) => v)
                    .map(([k, v]) => (
                      <div className="kv" key={k}>
                        <span className="kv-k">{k}</span>
                        <span className="kv-v">{v}</span>
                      </div>
                    ))}
                </div>
                {(resume.parsed?.skills || []).length > 0 && (
                  <div className="tag-list mt-12">
                    {resume.parsed.skills.slice(0, 12).map((s) => (
                      <span className="chip chip--accent" key={s}>
                        {s}
                      </span>
                    ))}
                  </div>
                )}
                {(resume.warnings || []).length > 0 && (
                  <div className="alert alert--warn mt-12">
                    <IconWarn size={17} />
                    <span>{resume.warnings.join('；')}</span>
                  </div>
                )}
                {/* 体验增强 §7：字段覆盖率 + 缺失项提示（编辑弹窗里可直接补录） */}
                <CoverageCard coverage={resume.coverage} />
              </div>

              {resumeSections.map((sec) => (
                <div className="mt-12" key={sec.label}>
                  <div className="block-label">
                    {sec.label}（{sec.items.length}）
                  </div>
                  <ul className="dot-list">
                    {sec.items.map((it, i) => (
                      <li key={i}>
                        <span>{it.head || '未命名'}</span>
                        {it.span ? <span className="mute-2">　{it.span}</span> : null}
                        {it.extra ? <span className="mute-2">　{String(it.extra).slice(0, 42)}</span> : null}
                      </li>
                    ))}
                  </ul>
                </div>
              ))}
              <div className="row row--wrap mt-12">
                <button className="btn btn--primary btn--sm" onClick={() => setResumeEditOpen(true)}>
                  确认 / 编辑
                </button>
                <label className="btn btn--ghost btn--sm" style={{ cursor: 'pointer' }}>
                  重新上传
                  <input
                    type="file"
                    accept="application/pdf,.pdf"
                    hidden
                    onChange={(e) => {
                      doUploadResume(e.target.files?.[0])
                      e.target.value = ''
                    }}
                  />
                </label>
                <button className="btn btn--ghost btn--sm" onClick={useDemoResume}>
                  用示例简历填充
                </button>
              </div>
            </div>
          ) : (
            <div>
              <label
                className="dropzone"
                onDragOver={(e) => {
                  e.preventDefault()
                  e.currentTarget.classList.add('over')
                }}
                onDragLeave={(e) => e.currentTarget.classList.remove('over')}
                onDrop={(e) => {
                  e.preventDefault()
                  e.currentTarget.classList.remove('over')
                  doUploadResume(e.dataTransfer?.files?.[0])
                }}
              >
                <input
                  type="file"
                  accept="application/pdf,.pdf"
                  hidden
                  onChange={(e) => {
                    doUploadResume(e.target.files?.[0])
                    e.target.value = ''
                  }}
                />
                <IconUpload size={26} style={{ color: 'var(--text-mute)' }} />
                <div className="dropzone-title">拖拽 PDF 简历到此处，或点击选择文件</div>
                <div className="dropzone-sub">仅支持 PDF，大小 ≤ {MAX_MB}MB；解析后可手动修正</div>
              </label>
              <div className="row row--wrap mt-12">
                <button className="btn btn--ghost btn--sm" onClick={useDemoResume}>
                  <IconPlay size={17} /> 用示例简历直接开始
                </button>
                <button className="btn btn--ghost btn--sm" onClick={() => setPasteOpen((v) => !v)}>
                  没有 PDF？粘贴简历文本
                </button>
              </div>
              {pasteOpen && (
                <div className="paste-box mt-12">
                  <div className="field-label">
                    粘贴简历文本，系统会自动结构化抽取教育/实习/项目/技能，解析后可再手动修正
                  </div>
                  <textarea
                    className="textarea mt-8"
                    value={pasteText}
                    placeholder="把简历内容整段粘贴到这里，例如：&#10;林宇航｜华南理工大学 软件工程 本科 大三&#10;教育背景 …&#10;实习经历 …&#10;项目经历 …&#10;专业技能 …"
                    onChange={(e) => setPasteText(e.target.value)}
                  />
                  <div className="row row--wrap mt-8">
                    <button className="btn btn--primary btn--sm" onClick={startManualResume}>
                      解析简历文本
                    </button>
                    <button className="btn btn--ghost btn--sm" onClick={useDemoResume}>
                      改用示例简历
                    </button>
                  </div>
                </div>
              )}
            </div>
          )}
        </SectionCard>

        {/* ================= ② JD ================= */}
        <SectionCard
          index="②"
          title="岗位 JD"
          desc="粘贴岗位描述自动解析，也可上传 PDF 版 JD"
          right={jd ? <span className={`chip ${jdBadgeClass}`}>{jdBadge}</span> : <span className="chip">未解析</span>}
        >
          {jdLoading ? (
            <SkeletonBlock lines={4} />
          ) : jd ? (
            <div>
              <div className="parse-summary">
                <div className="row row--between row--wrap">
                  <span className="chip chip--primary">
                    {jd.parsed?.basic?.title || '目标岗位'}
                    {jd.parsed?.basic?.department ? ` · ${jd.parsed.basic.department}` : ''}
                  </span>
                  <span className={`chip ${jdBadgeClass}`}>{jdBadge}</span>
                </div>
                {(jd.parsed?.responsibilities || []).length > 0 && (
                  <>
                    <div className="block-label">岗位职责</div>
                    <ul className="dot-list">
                      {jd.parsed.responsibilities.slice(0, 6).map((r, i) => (
                        <li key={i}>{r}</li>
                      ))}
                    </ul>
                  </>
                )}
                <div className="block-label">硬技能要求</div>
                <div className="tag-list">
                  {(jd.parsed?.requirements?.hard_skills || []).map((s) => (
                    <span className="chip chip--accent" key={s}>
                      {s}
                    </span>
                  ))}
                  {!(jd.parsed?.requirements?.hard_skills || []).length && (
                    <span className="mute-2" style={{ fontSize: 12 }}>
                      未识别到硬技能，可编辑补充
                    </span>
                  )}
                </div>
                <div className="block-label">软素质要求</div>
                <div className="tag-list">
                  {(jd.parsed?.requirements?.soft_skills || []).map((s) => (
                    <span className="chip" key={s}>
                      {s}
                    </span>
                  ))}
                  {!(jd.parsed?.requirements?.soft_skills || []).length && (
                    <span className="mute-2" style={{ fontSize: 12 }}>
                      未识别到软素质，可编辑补充
                    </span>
                  )}
                </div>
              </div>
              {(jd.warnings || []).length > 0 && (
                <div className="alert alert--warn mt-12">
                  <IconWarn size={17} />
                  <span>{jd.warnings.join('；')}</span>
                </div>
              )}
              {/* 体验增强 §7：字段覆盖率（缺失项可在编辑弹窗里直接补录） */}
              <CoverageCard coverage={jd.coverage} />
              <div className="row row--wrap mt-12">
                <button className="btn btn--primary btn--sm" onClick={() => setJdEditOpen(true)}>
                  确认 / 编辑
                </button>
                <button
                  className="btn btn--ghost btn--sm"
                  onClick={() => {
                    setJd(null)
                    setJdText('')
                  }}
                >
                  重新粘贴
                </button>
              </div>
            </div>
          ) : (
            <div>
              <div className="form-grid form-grid--2">
                <div className="field">
                  <span className="field-label">岗位名称（可选，帮助更准解析）</span>
                  <input
                    className="input"
                    value={jdTitle}
                    placeholder="例：产品经理（数据方向）"
                    onChange={(e) => setJdTitle(e.target.value)}
                  />
                </div>
              </div>
              <textarea
                className="textarea mt-12"
                value={jdText}
                placeholder="把招聘网站上的岗位描述整段粘贴到这里（职责、要求、加分项）…"
                onChange={(e) => setJdText(e.target.value)}
              />
              <div className="row row--wrap mt-12">
                <button className="btn btn--primary btn--sm" onClick={doParseJd}>
                  解析 JD
                </button>
                <label className="btn btn--ghost btn--sm" style={{ cursor: 'pointer' }}>
                  上传 PDF 版 JD
                  <input
                    type="file"
                    accept="application/pdf,.pdf"
                    hidden
                    onChange={(e) => {
                      doUploadJd(e.target.files?.[0])
                      e.target.value = ''
                    }}
                  />
                </label>
              </div>
            </div>
          )}
        </SectionCard>

        {/* ================= ③ 公司 ================= */}
        <SectionCard
          index="③"
          title="目标公司"
          desc="搜索内置公司库自动匹配画像，未收录可手动填写"
          right={companyId ? <span className="chip chip--ok">已选择</span> : <span className="chip">未选择</span>}
        >
          <div className="field">
            <span className="field-label">公司名称</span>
            <div className="company-search">
              <input
                className="input"
                value={companyQuery}
                placeholder="输入公司名，如「字节」「腾讯」「中国银行」"
                onChange={(e) => setCompanyQuery(e.target.value)}
              />
              {companySearching && <span className="spinner spinner--primary company-search-loading" />}
            </div>
            {companyHits.length > 0 && (
              <div className="suggest-list">
                {companyHits.map((c) => (
                  <button key={c.company_id} className="suggest-item" onClick={() => pickCompany(c)}>
                    <span className="suggest-name">{c.name}</span>
                    <span className="suggest-meta">
                      {[c.industry, c.scale, c.interview_style].filter(Boolean).join(' · ')}
                    </span>
                  </button>
                ))}
              </div>
            )}
          </div>

          {companyProfile ? (
            <div className="parse-summary mt-12">
              <div className="row row--between row--wrap">
                <span className="chip chip--primary">{companyProfile.name}</span>
                <span className="chip chip--ok">{companyProfile.interview_style || '结构化面'}</span>
              </div>
              {companyProfile.style_desc && <div className="summary-line">{companyProfile.style_desc}</div>}
              <div className="kv-grid">
                {[
                  ['行业', companyProfile.industry],
                  ['规模', companyProfile.scale],
                  ['总部', companyProfile.headquarters],
                  ['业务', companyProfile.business],
                ]
                  .filter(([, v]) => v)
                  .map(([k, v]) => (
                    <div className="kv" key={k}>
                      <span className="kv-k">{k}</span>
                      <span className="kv-v">{v}</span>
                    </div>
                  ))}
              </div>
              {(companyProfile.culture_tags || []).length > 0 && (
                <div className="tag-list mt-12">
                  {companyProfile.culture_tags.map((t) => (
                    <span className="chip chip--accent" key={t}>
                      {t}
                    </span>
                  ))}
                </div>
              )}
              {(companyProfile.common_questions || []).length > 0 && (
                <>
                  <div className="block-label">该公司常被问到的题</div>
                  <ul className="dot-list">
                    {companyProfile.common_questions.slice(0, 3).map((q, i) => (
                      <li key={i}>{q}</li>
                    ))}
                  </ul>
                </>
              )}
              <div className="row row--wrap mt-12">
                <button className="btn btn--ghost btn--sm" onClick={() => openManual(companyName)}>
                  手动修正公司信息
                </button>
                <span className="mute-2" style={{ fontSize: 12 }}>
                  {companyProfile.note || '信息仅供参考'}
                </span>
              </div>
            </div>
          ) : companyName ? (
            <div className="alert mt-12">
              <IconInfo size={17} />
              <span>
                已选择「{companyName}」，但暂未获取到公司画像。
                <button className="link-btn" onClick={() => openManual(companyName)}>
                  手动填写公司信息
                </button>
              </span>
            </div>
          ) : (
            <div className="mute-2 mt-12" style={{ fontSize: 12.5 }}>
              公司信息用于调整面试官风格（压力面 / 结构化面）与提问重点；不选也能开始面试。
            </div>
          )}
        </SectionCard>

        {/* ================= ④ 匹配度 ================= */}
        <SectionCard
          index="④"
          title="匹配度分析"
          desc="简历 × JD × 公司三维交叉分析"
          right={
            <button
              className="btn btn--ghost btn--sm"
              onClick={() => runMatch(true)}
              disabled={!canStart || matchLoading}
            >
              {matchLoading ? '计算中…' : '重新计算'}
            </button>
          }
        >
          {!canStart ? (
            <div className="alert">
              <IconLock size={17} />
              <span>完成「简历」与「岗位 JD」后，这里会自动算出匹配度、薄弱项与提问策略。</span>
            </div>
          ) : matchLoading ? (
            <div className="row gap-12" style={{ alignItems: 'center' }}>
              <div className="skeleton" style={{ width: 120, height: 120, borderRadius: '50%' }} />
              <div className="grow">
                <div className="skeleton sk-line" />
                <div className="skeleton sk-line sk-line--60" />
                <div className="skeleton sk-line sk-line--40" />
              </div>
            </div>
          ) : matchError ? (
            <div className="alert alert--danger">
              <IconWarn size={17} />
              <span>
                {matchError}
                <button className="link-btn" onClick={() => runMatch(true)}>
                  重试
                </button>
              </span>
            </div>
          ) : match ? (
            <div>
              <div className="match-top">
                <ScoreRing value={match.overall} size={124} label="整体匹配度" />
                <div className="grow">
                  <div className="match-level">
                    {match.level || '—'}
                    {/* 趋势标记：只报告"哪一维动了多少"，不编造原因（体验增强 §8） */}
                    {matchTrend && matchTrend.delta !== 0 && (
                      <span
                        className={`chip ${matchTrend.delta > 0 ? 'chip--ok' : 'chip--danger'}`}
                        title="与上一次同一份简历 + 同一个岗位的计算结果相比"
                      >
                        {matchTrend.delta > 0 ? '↑' : '↓'}
                        {Math.abs(matchTrend.delta)}
                        {matchTrend.top && Math.abs(matchTrend.top.delta) >= 0.1
                          ? `（主要来自 ${matchTrend.top.name} ${
                              matchTrend.top.delta > 0 ? '+' : ''
                            }${matchTrend.top.delta.toFixed(1)}）`
                          : ''}
                      </span>
                    )}
                  </div>
                  <div className="block-label">
                    匹配明细
                    <span className="mute-2" style={{ fontWeight: 400 }}>
                      （点任意一维展开：命中了什么、缺什么、怎么补）
                    </span>
                  </div>
                  <div className="bars">
                    {(match.breakdown || []).map((b) => {
                      const key = b.key || b.dimension
                      const on = openDim === key
                      return (
                        <div className="bar-block" key={key}>
                          <button
                            type="button"
                            className={`bar-row${on ? ' on' : ''}`}
                            aria-expanded={on}
                            onClick={() => setOpenDim(on ? '' : key)}
                          >
                            <span className="bar-name">{b.dimension}</span>
                            <span className="bar-track">
                              <i style={{ width: `${Math.round(((b.score || 0) / 10) * 100)}%` }} />
                            </span>
                            <span className="bar-val mono">{(b.score || 0).toFixed(1)}</span>
                            <span className="bar-arrow">{on ? '▲' : '▼'}</span>
                          </button>

                          {on && (
                            <div className="dim-drill fade-up">
                              <p className="dim-drill-detail">{b.detail}</p>
                              {b.weight_hint && (
                                <p className="mute-2" style={{ fontSize: 12 }}>
                                  {b.weight_hint}
                                </p>
                              )}
                              {(b.hit_keywords || []).length > 0 && (
                                <div className="drill-line">
                                  <span className="mute-2">已命中</span>
                                  {(b.hit_keywords || []).map((k) => (
                                    <span className="chip chip--ok" key={k}>
                                      {k}
                                    </span>
                                  ))}
                                </div>
                              )}
                              {(b.miss_keywords || []).length > 0 && (
                                <div className="drill-line">
                                  <span className="mute-2">缺失</span>
                                  {(b.miss_keywords || []).map((k) => (
                                    <span className="chip chip--warn" key={k}>
                                      {k}
                                    </span>
                                  ))}
                                </div>
                              )}
                              {b.suggestion && (
                                <p
                                  className={`drill-suggest${
                                    (b.miss_keywords || []).length ? '' : ' drill-suggest--ok'
                                  }`}
                                >
                                  {(b.miss_keywords || []).length ? '怎么补：' : ''}
                                  {b.suggestion}
                                </p>
                              )}
                            </div>
                          )}
                        </div>
                      )
                    })}
                    {!(match.breakdown || []).length && (
                      <div className="mute-2" style={{ fontSize: 12.5 }}>
                        暂无分项数据
                      </div>
                    )}
                  </div>
                </div>
              </div>

              <div className="match-lists mt-16">
                <div>
                  <div className="block-label ok"><IconCheckCircle size={15} /> 匹配项</div>
                  <ul className="dot-list dot-list--ok">
                    {(match.matched || []).map((m, i) => (
                      <li key={i}>
                        <b>{m.dimension}</b>：{m.detail}
                      </li>
                    ))}
                    {!(match.matched || []).length && <li className="mute-2">暂无</li>}
                  </ul>
                </div>
                <div>
                  <div className="block-label warn"><IconWarn size={15} /> 薄弱项</div>
                  <ul className="dot-list dot-list--warn">
                    {(match.weak || []).map((m, i) => (
                      <li key={i}>
                        <b>{m.dimension}</b>：{m.detail}
                      </li>
                    ))}
                    {!(match.weak || []).length && <li className="mute-2">暂无</li>}
                  </ul>
                </div>
              </div>

              <div className="block-label"><IconCompass size={15} /> 提问策略</div>
              <div className="strategy-grid">
                {[
                  ['重点考察', match.strategy?.focus, 'chip--primary'],
                  ['深挖经历', match.strategy?.deep_dive, 'chip--accent'],
                  ['压力测试', match.strategy?.pressure_test, 'chip--warn'],
                ].map(([label, list, cls]) => (
                  <div className="strategy-box" key={label}>
                    <div className={`chip ${cls}`}>{label}</div>
                    <ul className="dot-list">
                      {(list || []).map((s, i) => (
                        <li key={i}>{s}</li>
                      ))}
                      {!(list || []).length && <li className="mute-2">暂无</li>}
                    </ul>
                  </div>
                ))}
              </div>

              {/* 体验增强 §8：同一份简历挂多个 JD 的对比（投递季的真实需求） */}
              {compareList.length > 1 && (
                <>
                  <div className="block-label mt-16">
                    <IconCompass size={15} /> 换个岗位比比看
                    <span className="mute-2" style={{ fontWeight: 400 }}>
                      （同一份简历对多个 JD，点卡片可切换目标岗位）
                    </span>
                  </div>
                  <div className="compare-jd">
                    {compareList.map((c) => {
                      // 「差在哪一项」= 与本次最高的那一维相比，差距最大的维度
                      const best = Math.max(...compareList.map((x) => x.overall || 0))
                      const gapDim = (() => {
                        if (c.isCurrent || c.overall >= best) return null
                        const top = [...compareList].sort((a, b) => b.overall - a.overall)[0]
                        const mine = {}
                        for (const b of c.breakdown || []) mine[b.key] = Number(b.score) || 0
                        let worst = null
                        for (const b of top?.breakdown || []) {
                          const d = (Number(b.score) || 0) - (mine[b.key] ?? 0)
                          if (!worst || d > worst.d) worst = { name: b.dimension, d }
                        }
                        return worst && worst.d > 0.2 ? worst : null
                      })()
                      return (
                        <button
                          type="button"
                          key={c.jdId}
                          className={`compare-card${c.isCurrent ? ' on' : ''}`}
                          onClick={() => switchJd(c.jdId)}
                          aria-current={c.isCurrent}
                        >
                          <div className="compare-card-head">
                            <span className="compare-title">{c.title}</span>
                            {c.isCurrent && <span className="chip chip--primary">当前</span>}
                          </div>
                          {c.department && <span className="mute-2" style={{ fontSize: 12 }}>{c.department}</span>}
                          <div className="compare-score mono">{c.overall}</div>
                          <div className="mute-2" style={{ fontSize: 12 }}>
                            {gapDim
                              ? `差在「${gapDim.name}」${gapDim.d.toFixed(1)} 分`
                              : c.isCurrent
                                ? '正在看的岗位'
                                : '与最优岗位持平'}
                          </div>
                        </button>
                      )
                    })}
                  </div>
                </>
              )}
              {compareLoading && (
                <div className="mute-2 mt-8" style={{ fontSize: 12 }}>
                  正在对比其它岗位…
                </div>
              )}
            </div>
          ) : (
            <div className="empty">
              <div className="empty-desc">点击「重新计算」生成匹配度分析</div>
            </div>
          )}
        </SectionCard>

        {/* ================= ⑤ 面试设置 =================
            这一块是页面的「动作区」（选面试官 / 作答方式 / 开始按钮），
            因此在两列布局下**横跨整行**：既能容纳表单，也不会让最后一排剩下半格空位。 */}
        <SectionCard
          index="⑤"
          title="面试设置"
          desc="选好面试官与作答方式，开始一场 20 分钟左右的模拟"
          className="setup-span"
        >
          <div className="setting-block">
            <div className="block-label">面试官音色</div>
            <div className="form-grid form-grid--2">
              {interviewerOptions.map((opt) => (
                <button
                  key={opt.key}
                  className={`opt ${interviewer === opt.key ? 'on' : ''}`}
                  onClick={() => setInterviewer(opt.key)}
                >
                  <span className="opt-radio" />
                  <span className="grow">
                    <span className="opt-title">{opt.label}</span>
                    <span className="opt-desc">{opt.style || ''}</span>
                  </span>
                  <span style={{ width: 56, height: 56, flex: 'none', overflow: 'hidden', display: 'grid', placeItems: 'center' }}>
                    <Avatar gender={opt.key} speaking={false} volume={0} />
                  </span>
                </button>
              ))}
            </div>
          </div>

          <div className="setting-block">
            <div className="block-label">作答方式</div>
            <div className="seg">
              <button className={mode === 'voice' ? 'on' : ''} onClick={() => setMode('voice')}>
                语音回答
              </button>
              <button className={mode === 'text' ? 'on' : ''} onClick={() => setMode('text')}>
                文字回答
              </button>
            </div>
            {!asrOK && (
              <div className="alert alert--warn mt-8">
                <IconWarn size={17} />
                <span>当前浏览器不支持语音识别，已自动切换为文字模式（推荐 Chrome / Edge）。</span>
              </div>
            )}
          </div>

          {/* 体验增强 §3：面试前 30 秒设备自检。
              提前暴露「麦克风不可用」这类问题，避免答到第 3 题才发现没声音。 */}
          <div className="setting-block">
            <div className="row row--between row--wrap gap-8">
              <div className="block-label" style={{ margin: 0 }}>
                <IconMic size={15} className="inline-ic" /> 开麦前自检
              </div>
              {deviceChecked ? (
                <span className="chip chip--ok">已完成自检</span>
              ) : (
                <span className="chip">建议先花 30 秒</span>
              )}
            </div>
            <p className="mute-2 mt-8" style={{ fontSize: 12, lineHeight: 1.6 }}>
              检查浏览器能力、麦克风权限与面试官语音。全部失败路径都能落到文字模式，不会阻断面试。
            </p>
            <button className="btn btn--ghost btn--sm mt-8" onClick={() => setDevOpen(true)}>
              <IconMic size={16} /> {deviceChecked ? '重新自检' : '开始设备自检'}
            </button>
          </div>

          <div className="setting-block">
            <div className="block-label">题量</div>
            <div className="seg">
              {[6, 9, 12].map((n) => (
                <button key={n} className={questionCount === n ? 'on' : ''} onClick={() => setQuestionCount(n)}>
                  {n} 题
                </button>
              ))}
            </div>
            <div className="mute-2 mt-8" style={{ fontSize: 12 }}>
              覆盖自我介绍 → 项目深挖 → 专业技能 → 场景题 → 行为面试 → 反问环节
            </div>
          </div>

          {/* 体验增强 §14：压力面试模式（练习模式，报告里会标注，避免与其他场次直接比分数） */}
          <div className="setting-block">
            <label className="pressure-toggle">
              <input
                type="checkbox"
                checked={pressureMode}
                onChange={(e) => setPressureMode(e.target.checked)}
              />
              <span className="pressure-box" aria-hidden="true">
                {pressureMode ? <IconCheckCircle size={16} /> : null}
              </span>
              <span className="grow">
                <span className="pressure-title">压力面试模式</span>
                <span className="mute-2 pressure-desc">
                  追问更密、语气更直接，偏压力测试题；适合已经练过几场、想提前适应高压节奏。
                  这是练习模式，报告里会标注，不会与其他场次直接比分数。
                </span>
              </span>
            </label>
          </div>

          {/* 开始面试前不再有任何次数提示：所有登录用户不限次数、永久免费 */}
          <button className="btn btn--primary btn--lg btn--block mt-16" onClick={onStart} disabled={starting || !canStart}>
            {starting ? <span className="spinner" /> : <IconPlay size={18} />}
            {starting ? '正在创建面试…' : canStart ? '开始模拟面试' : '请先完成简历与 JD'}
          </button>
          {canStart ? <p className="setup-start-hint">不限次数，想练几场就练几场。</p> : null}
        </SectionCard>
      </div>

      {/* ================= 简历编辑弹窗 ================= */}
      <ResumeEditModal
        open={resumeEditOpen}
        initial={resume?.parsed}
        rawText={pasteText}
        saving={resumeSaving}
        confidence={resume?.confidence_levels}
        onClose={() => setResumeEditOpen(false)}
        onSave={saveResume}
      />

      {/* ================= JD 编辑弹窗 ================= */}
      <JdEditModal
        open={jdEditOpen}
        initial={jd?.parsed}
        saving={jdSaving}
        confidence={jd?.confidence_levels}
        onClose={() => setJdEditOpen(false)}
        onSave={saveJd}
      />

      {/* ================= 公司手动填写弹窗 ================= */}
      <Modal
        open={companyManualOpen}
        title="手动填写公司信息"
        desc="填写后用于调整面试官风格与提问重点"
        loading={companySaving}
        onClose={() => setCompanyManualOpen(false)}
        footer={
          <div className="row row--between">
            <button className="btn btn--ghost" onClick={() => setCompanyManualOpen(false)} disabled={companySaving}>
              取消
            </button>
            <button className="btn btn--primary" onClick={saveManualCompany} disabled={companySaving}>
              {companySaving ? <span className="spinner" /> : '保存'}
            </button>
          </div>
        }
      >
        <div className="form-grid form-grid--2">
          <div className="field">
            <span className="field-label">公司名称 *</span>
            <input
              className="input"
              value={companyManualDraft.name}
              onChange={(e) => setCompanyManualDraft((d) => ({ ...d, name: e.target.value }))}
            />
          </div>
          <div className="field">
            <span className="field-label">行业</span>
            <input
              className="input"
              value={companyManualDraft.industry}
              onChange={(e) => setCompanyManualDraft((d) => ({ ...d, industry: e.target.value }))}
            />
          </div>
          <div className="field">
            <span className="field-label">公司规模</span>
            <input
              className="input"
              value={companyManualDraft.scale}
              onChange={(e) => setCompanyManualDraft((d) => ({ ...d, scale: e.target.value }))}
            />
          </div>
          <div className="field">
            <span className="field-label">面试风格</span>
            <select
              className="select"
              value={companyManualDraft.interview_style}
              onChange={(e) => setCompanyManualDraft((d) => ({ ...d, interview_style: e.target.value }))}
            >
              {['结构化面', '压力面', '温和面', '追问式'].map((s) => (
                <option key={s} value={s}>
                  {s}
                </option>
              ))}
            </select>
          </div>
        </div>
        <div className="field mt-12">
          <span className="field-label">主营业务</span>
          <input
            className="input"
            value={companyManualDraft.business}
            onChange={(e) => setCompanyManualDraft((d) => ({ ...d, business: e.target.value }))}
          />
        </div>
        <div className="field mt-12">
          <span className="field-label">文化标签</span>
          <TagInput
            value={companyManualDraft.culture_tags}
            onChange={(v) => setCompanyManualDraft((d) => ({ ...d, culture_tags: v }))}
            placeholder="如：数据驱动 / 扁平化，回车添加"
          />
        </div>
        <div className="field mt-12">
          <span className="field-label">面试风格说明</span>
          <textarea
            className="textarea"
            value={companyManualDraft.style_desc}
            onChange={(e) => setCompanyManualDraft((d) => ({ ...d, style_desc: e.target.value }))}
          />
        </div>
      </Modal>

      {/* ================= 设备自检（体验增强 §3）=================
          onDone 在进入结论页时回调（不是关闭时），所以这里不关弹窗 */}
      <DeviceCheck
        open={devOpen}
        mode={mode}
        ttsProvider={voice.tts}
        asrProvider={voice.asr}
        onClose={() => setDevOpen(false)}
        onDone={(result) => {
          setDeviceChecked(true)
          saveLocal('aic_device_checked', true)
          if (result?.textMode && mode === 'voice') {
            setMode('text')
            notify('自检建议改用文字模式，已为你切换（流程可完整走完）', 'warn', 4200)
          } else {
            notify(result?.summary || '设备自检完成', result?.ok ? 'success' : 'warn', 3600)
          }
        }}
      />
    </div>
  )
}

/* ==========================================================================
   简历编辑弹窗
   ========================================================================== */
/**
 * 字段置信度标记（体验增强 §7）。
 *
 * 只描述"这份信息是怎么被找到的"：高=有明确板块标题或标签，中=标题不明显或字段不完整，
 * 低=AI 推测或没找到。阈值由后端统一下发（`confidence_levels[].level`），前端不再定档。
 */
const CONF_META = {
  high: { text: '高', cls: 'cf--high', tip: '有明确的板块标题或标签，可直接采信' },
  mid: { text: '中', cls: 'cf--mid', tip: '标题不明显或字段不完整，建议扫一眼' },
  low: { text: '低', cls: 'cf--low', tip: 'AI 推测或未识别到，请重点核对/补录' },
}

function ConfidenceBadge({ item }) {
  if (!item) return null
  const meta = CONF_META[item.level] || CONF_META.low
  const pct = Math.round((Number(item.confidence) || 0) * 100)
  return (
    <span className={`cf-badge ${meta.cls}`} title={`${meta.tip}（置信度 ${pct}%）`}>
      {meta.text}
    </span>
  )
}

/** 覆盖率卡片：已识别 N/M 项 + 缺失项可直接补录（体验增强 §7） */
function CoverageCard({ coverage }) {
  if (!coverage || !coverage.total) return null
  const { recognized = 0, total = 0, missing = [], note = '' } = coverage
  return (
    <div className="coverage-card">
      <div className="row row--between row--wrap gap-8">
        <span className="block-label" style={{ margin: 0 }}>
          <IconCheckCircle size={15} className="inline-ic" /> 字段覆盖率
        </span>
        <span className={`chip mono ${recognized === total ? 'chip--ok' : 'chip--warn'}`}>
          已识别 {recognized}/{total} 项
        </span>
      </div>
      {missing.length > 0 && (
        <div className="coverage-missing">
          <span className="mute-2">缺失：</span>
          {missing.map((m) => (
            <span className="chip" key={m}>
              {m}
            </span>
          ))}
        </div>
      )}
      {note && (
        <p className="mute-2" style={{ fontSize: 12, lineHeight: 1.6, margin: '8px 0 0' }}>
          {note}
        </p>
      )}
    </div>
  )
}

function ResumeEditModal({ open, initial, rawText, saving, onClose, onSave, confidence }) {
  const [draft, setDraft] = useState(emptyResume())

  useEffect(() => {
    if (open) setDraft(normResume(initial))
  }, [open, initial])

  const setBasic = (k, v) => setDraft((d) => ({ ...d, basic: { ...d.basic, [k]: v } }))

  /** 置信度按 key 建索引，字段旁直接取用 */
  const confByKey = useMemo(() => {
    const map = {}
    for (const item of confidence || []) map[item.key] = item
    return map
  }, [confidence])

  // 需要核对的字段按置信度升序 = 最该先改的排最前
  const todo = useMemo(
    () => (confidence || []).filter((c) => c.level !== 'high'),
    [confidence]
  )

  const jumpTo = (key) => {
    const el = document.getElementById(`cf-${String(key).replace(/\./g, '-')}`)
    if (!el) return
    el.scrollIntoView({ block: 'center', behavior: 'smooth' })
    // 输入框直接聚焦；板块级（div/span）不可聚焦，滚动到位即可
    if (typeof el.focus === 'function' && /^(INPUT|TEXTAREA|SELECT)$/.test(el.tagName)) el.focus()
  }

  const setIntern = (idx, patch) =>
    setDraft((d) => ({
      ...d,
      internships: d.internships.map((it, i) => (i === idx ? { ...it, ...patch } : it)),
    }))
  const setProject = (idx, patch) =>
    setDraft((d) => ({
      ...d,
      projects: d.projects.map((it, i) => (i === idx ? { ...it, ...patch } : it)),
    }))
  const setCampus = (idx, patch) =>
    setDraft((d) => ({
      ...d,
      campus_experience: d.campus_experience.map((it, i) => (i === idx ? { ...it, ...patch } : it)),
    }))

  return (
    <Modal
      open={open}
      wide
      title="确认 / 编辑简历档案"
      desc="解析结果可能有个别偏差，请按实际情况修正后再开始面试"
      loading={saving}
      onClose={onClose}
      footer={
        <div className="row row--between">
          <button className="btn btn--ghost" onClick={onClose} disabled={saving}>
            取消
          </button>
          <button className="btn btn--primary" onClick={() => onSave(draft)} disabled={saving}>
            {saving ? <span className="spinner" /> : '保存档案'}
          </button>
        </div>
      }
    >
      {rawText && (
        <details className="raw-box">
          <summary>查看你粘贴的简历原文（对照填写）</summary>
          <pre>{rawText}</pre>
        </details>
      )}

      {/* 把"从头看一遍"变成"只改这几处"：低置信度字段按优先级列在最前面 */}
      {todo.length > 0 && (
        <div className="cf-todo" role="note">
          <div className="cf-todo-head">
            <IconWarn size={15} />
            <span className="grow">
              建议优先核对这 {todo.length} 项（按"最可能是 AI 猜的"排序）
            </span>
          </div>
          <div className="cf-todo-list">
            {todo.map((c) => (
              <button type="button" className="cf-todo-item" key={c.key} onClick={() => jumpTo(c.key)}>
                <ConfidenceBadge item={c} />
                <span>{c.label}</span>
              </button>
            ))}
          </div>
          <div className="mute-2" style={{ fontSize: 12 }}>
            置信度只表示"这份信息是怎么被找到的"，不代表内容一定错——扫一眼比全部重填快得多。
          </div>
        </div>
      )}

      <div className="block-label">基本信息</div>
      <div className="form-grid form-grid--3">
        {[
          ['name', '姓名'],
          ['school', '学校'],
          ['major', '专业'],
          ['degree', '学历（本科/硕士）'],
          ['grade', '年级'],
        ].map(([k, label]) => {
          const item = confByKey[`basic.${k}`]
          const low = item && item.level !== 'high'
          return (
            <div className={`field${low ? ' field--check' : ''}`} key={k}>
              <span className="field-label">
                {label}
                <ConfidenceBadge item={item} />
              </span>
              <input
                className="input"
                id={`cf-basic-${k}`}
                value={draft.basic[k] || ''}
                onChange={(e) => setBasic(k, e.target.value)}
              />
            </div>
          )
        })}
      </div>

      <div className={`field mt-12${confByKey.skills && confByKey.skills.level !== 'high' ? ' field--check' : ''}`}>
        <span className="field-label">
          技能标签
          <ConfidenceBadge item={confByKey.skills} />
        </span>
        <TagInput value={draft.skills} onChange={(v) => setDraft((d) => ({ ...d, skills: v }))} placeholder="如：Python，回车添加" />
      </div>

      <div className="block-label row row--between" id="cf-internships">
        <span>
          实习经历（{draft.internships.length}）
          <ConfidenceBadge item={confByKey.internships} />
        </span>
        <button
          className="btn btn--ghost btn--sm"
          onClick={() =>
            setDraft((d) => ({
              ...d,
              internships: [...d.internships, { company: '', title: '', start: '', end: '', duties: [], achievements: [] }],
            }))
          }
        >
          + 添加实习
        </button>
      </div>
      {draft.internships.map((it, i) => (
        <div className="sub-card mt-8" key={i}>
          <div className="form-grid form-grid--2">
            <div className="field">
              <span className="field-label">公司</span>
              <input className="input" value={it.company || ''} onChange={(e) => setIntern(i, { company: e.target.value })} />
            </div>
            <div className="field">
              <span className="field-label">岗位</span>
              <input className="input" value={it.title || ''} onChange={(e) => setIntern(i, { title: e.target.value })} />
            </div>
            <div className="field">
              <span className="field-label">开始</span>
              <input className="input" value={it.start || ''} onChange={(e) => setIntern(i, { start: e.target.value })} placeholder="2024.07" />
            </div>
            <div className="field">
              <span className="field-label">结束</span>
              <input className="input" value={it.end || ''} onChange={(e) => setIntern(i, { end: e.target.value })} placeholder="2024.10" />
            </div>
          </div>
          <div className="field mt-8">
            <span className="field-label">职责（每行一条）</span>
            <textarea
              className="textarea"
              value={arrToText(it.duties)}
              onChange={(e) => setIntern(i, { duties: textToArr(e.target.value) })}
            />
          </div>
          <div className="field mt-8">
            <span className="field-label">成果（每行一条，尽量量化）</span>
            <textarea
              className="textarea"
              value={arrToText(it.achievements)}
              onChange={(e) => setIntern(i, { achievements: textToArr(e.target.value) })}
            />
          </div>
          <div className="row row--between mt-8">
            <span />
            <button
              className="btn btn--danger btn--sm"
              onClick={() => setDraft((d) => ({ ...d, internships: d.internships.filter((_, idx) => idx !== i) }))}
            >
              删除这段实习
            </button>
          </div>
        </div>
      ))}

      <div className="block-label row row--between" id="cf-projects">
        <span>
          项目经历（{draft.projects.length}）
          <ConfidenceBadge item={confByKey.projects} />
        </span>
        <button
          className="btn btn--ghost btn--sm"
          onClick={() =>
            setDraft((d) => ({
              ...d,
              projects: [...d.projects, { name: '', role: '', start: '', end: '', description: '', tech_stack: [] }],
            }))
          }
        >
          + 添加项目
        </button>
      </div>
      {draft.projects.map((it, i) => (
        <div className="sub-card mt-8" key={i}>
          <div className="form-grid form-grid--2">
            <div className="field">
              <span className="field-label">项目名称</span>
              <input className="input" value={it.name || ''} onChange={(e) => setProject(i, { name: e.target.value })} />
            </div>
            <div className="field">
              <span className="field-label">担任角色</span>
              <input className="input" value={it.role || ''} onChange={(e) => setProject(i, { role: e.target.value })} />
            </div>
          </div>
          <div className="field mt-8">
            <span className="field-label">项目描述</span>
            <textarea
              className="textarea"
              value={it.description || ''}
              onChange={(e) => setProject(i, { description: e.target.value })}
            />
          </div>
          <div className="field mt-8">
            <span className="field-label">技术栈 / 工具</span>
            <TagInput value={it.tech_stack || []} onChange={(v) => setProject(i, { tech_stack: v })} placeholder="如：SQL，回车添加" />
          </div>
          <div className="row row--between mt-8">
            <span />
            <button
              className="btn btn--danger btn--sm"
              onClick={() => setDraft((d) => ({ ...d, projects: d.projects.filter((_, idx) => idx !== i) }))}
            >
              删除这个项目
            </button>
          </div>
        </div>
      ))}

      <div className="block-label row row--between" id="cf-campus_experience">
        <span>
          校园经历（{draft.campus_experience.length}）
          <ConfidenceBadge item={confByKey.campus_experience} />
        </span>
        <button
          className="btn btn--ghost btn--sm"
          onClick={() =>
            setDraft((d) => ({
              ...d,
              campus_experience: [
                ...d.campus_experience,
                { name: '', role: '', start: '', end: '', description: '', highlights: [] },
              ],
            }))
          }
        >
          + 添加校园经历
        </button>
      </div>
      {draft.campus_experience.map((it, i) => (
        <div className="sub-card mt-8" key={i}>
          <div className="form-grid form-grid--2">
            <div className="field">
              <span className="field-label">组织 / 活动名称</span>
              <input
                className="input"
                value={it.name || ''}
                placeholder="如：校学生会、摄影协会"
                onChange={(e) => setCampus(i, { name: e.target.value })}
              />
            </div>
            <div className="field">
              <span className="field-label">担任职务</span>
              <input
                className="input"
                value={it.role || ''}
                placeholder="如：副部长、社长、志愿者"
                onChange={(e) => setCampus(i, { role: e.target.value })}
              />
            </div>
            <div className="field">
              <span className="field-label">开始</span>
              <input
                className="input"
                value={it.start || ''}
                placeholder="2023.09"
                onChange={(e) => setCampus(i, { start: e.target.value })}
              />
            </div>
            <div className="field">
              <span className="field-label">结束</span>
              <input
                className="input"
                value={it.end || ''}
                placeholder="2024.06"
                onChange={(e) => setCampus(i, { end: e.target.value })}
              />
            </div>
          </div>
          <div className="field mt-8">
            <span className="field-label">主要工作</span>
            <textarea
              className="textarea"
              value={it.description || ''}
              placeholder="负责什么、组织了哪些活动"
              onChange={(e) => setCampus(i, { description: e.target.value })}
            />
          </div>
          <div className="field mt-8">
            <span className="field-label">成果亮点（每行一条，尽量量化）</span>
            <textarea
              className="textarea"
              value={arrToText(it.highlights)}
              placeholder="如：组织 4 场校级活动，累计参与 1200+ 人次"
              onChange={(e) => setCampus(i, { highlights: textToArr(e.target.value) })}
            />
          </div>
          <div className="row row--between mt-8">
            <span />
            <button
              className="btn btn--danger btn--sm"
              onClick={() =>
                setDraft((d) => ({
                  ...d,
                  campus_experience: d.campus_experience.filter((_, idx) => idx !== i),
                }))
              }
            >
              删除这段校园经历
            </button>
          </div>
        </div>
      ))}

      <div className={`field mt-12${confByKey.awards && confByKey.awards.level !== 'high' ? ' field--check' : ''}`} id="cf-awards">
        <span className="field-label">
          获奖荣誉
          <ConfidenceBadge item={confByKey.awards} />
        </span>
        <TagInput value={draft.awards} onChange={(v) => setDraft((d) => ({ ...d, awards: v }))} placeholder="如：校级一等奖学金，回车添加" />
      </div>
      <div
        className={`field mt-12${confByKey.self_evaluation && confByKey.self_evaluation.level !== 'high' ? ' field--check' : ''}`}
        id="cf-self_evaluation"
      >
        <span className="field-label">
          自我评价
          <ConfidenceBadge item={confByKey.self_evaluation} />
        </span>
        <textarea
          className="textarea"
          value={draft.self_evaluation || ''}
          onChange={(e) => setDraft((d) => ({ ...d, self_evaluation: e.target.value }))}
        />
      </div>
    </Modal>
  )
}

/* ==========================================================================
   JD 编辑弹窗
   ========================================================================== */
function JdEditModal({ open, initial, saving, onClose, onSave, confidence }) {
  const [draft, setDraft] = useState(emptyJd())

  useEffect(() => {
    if (open) setDraft(normJd(initial))
  }, [open, initial])

  const setReq = (k, v) =>
    setDraft((d) => ({ ...d, requirements: { ...d.requirements, [k]: v } }))

  /** 置信度索引 + 待核对清单（与简历弹窗同一套口径，level 由后端下发） */
  const confByKey = useMemo(() => {
    const map = {}
    for (const item of confidence || []) map[item.key] = item
    return map
  }, [confidence])
  const todo = useMemo(() => (confidence || []).filter((c) => c.level !== 'high'), [confidence])
  const checkCls = (key) => {
    const item = confByKey[key]
    return item && item.level !== 'high' ? ' field--check' : ''
  }

  return (
    <Modal
      open={open}
      wide
      title="确认 / 编辑岗位信息"
      desc="岗位职责与技能要求会影响提问重点与匹配度计算"
      loading={saving}
      onClose={onClose}
      footer={
        <div className="row row--between">
          <button className="btn btn--ghost" onClick={onClose} disabled={saving}>
            取消
          </button>
          <button className="btn btn--primary" onClick={() => onSave(draft)} disabled={saving}>
            {saving ? <span className="spinner" /> : '保存岗位信息'}
          </button>
        </div>
      }
    >
      {todo.length > 0 && (
        <div className="cf-todo" role="note">
          <div className="cf-todo-head">
            <IconWarn size={15} />
            <span className="grow">建议优先核对这 {todo.length} 项（按"最可能是 AI 猜的"排序）</span>
          </div>
          <div className="cf-todo-list">
            {todo.map((c) => (
              <button
                type="button"
                className="cf-todo-item"
                key={c.key}
                onClick={() => {
                  const el = document.getElementById(`jdcf-${String(c.key).replace(/\./g, '-')}`)
                  if (el) el.scrollIntoView({ block: 'center', behavior: 'smooth' })
                }}
              >
                <ConfidenceBadge item={c} />
                <span>{c.label}</span>
              </button>
            ))}
          </div>
        </div>
      )}

      <div className="form-grid form-grid--3">
        {[
          ['title', '岗位名称'],
          ['department', '部门'],
          ['location', '工作地点'],
        ].map(([k, label]) => (
          <div className={`field${checkCls(`basic.${k}`)}`} key={k} id={`jdcf-basic-${k}`}>
            <span className="field-label">
              {label}
              <ConfidenceBadge item={confByKey[`basic.${k}`]} />
            </span>
            <input
              className="input"
              value={draft.basic[k] || ''}
              onChange={(e) => setDraft((d) => ({ ...d, basic: { ...d.basic, [k]: e.target.value } }))}
            />
          </div>
        ))}
      </div>

      <div className={`field mt-12${checkCls('responsibilities')}`} id="jdcf-responsibilities">
        <span className="field-label">
          岗位职责（每行一条）
          <ConfidenceBadge item={confByKey.responsibilities} />
        </span>
        <textarea
          className="textarea"
          value={arrToText(draft.responsibilities)}
          onChange={(e) => setDraft((d) => ({ ...d, responsibilities: textToArr(e.target.value) }))}
        />
      </div>

      <div className="form-grid form-grid--2 mt-12">
        <div className={`field${checkCls('requirements.degree')}`} id="jdcf-requirements-degree">
          <span className="field-label">
            学历要求
            <ConfidenceBadge item={confByKey['requirements.degree']} />
          </span>
          <input className="input" value={draft.requirements.degree || ''} onChange={(e) => setReq('degree', e.target.value)} />
        </div>
        <div className={`field${checkCls('requirements.experience')}`} id="jdcf-requirements-experience">
          <span className="field-label">
            经验要求
            <ConfidenceBadge item={confByKey['requirements.experience']} />
          </span>
          <input
            className="input"
            value={draft.requirements.experience || ''}
            onChange={(e) => setReq('experience', e.target.value)}
          />
        </div>
      </div>

      <div className={`field mt-12${checkCls('requirements.hard_skills')}`} id="jdcf-requirements-hard_skills">
        <span className="field-label">
          硬技能要求
          <ConfidenceBadge item={confByKey['requirements.hard_skills']} />
        </span>
        <TagInput value={draft.requirements.hard_skills} onChange={(v) => setReq('hard_skills', v)} placeholder="如：SQL，回车添加" />
      </div>
      <div className={`field mt-12${checkCls('requirements.soft_skills')}`} id="jdcf-requirements-soft_skills">
        <span className="field-label">
          软素质要求
          <ConfidenceBadge item={confByKey['requirements.soft_skills']} />
        </span>
        <TagInput value={draft.requirements.soft_skills} onChange={(v) => setReq('soft_skills', v)} placeholder="如：沟通，回车添加" />
      </div>
      <div className={`field mt-12${checkCls('requirements.bonus')}`} id="jdcf-requirements-bonus">
        <span className="field-label">
          加分项（每行一条）
          <ConfidenceBadge item={confByKey['requirements.bonus']} />
        </span>
        <textarea
          className="textarea"
          value={arrToText(draft.requirements.bonus)}
          onChange={(e) => setReq('bonus', textToArr(e.target.value))}
        />
      </div>
      <div className={`field mt-12${checkCls('keywords')}`} id="jdcf-keywords">
        <span className="field-label">
          关键词
          <ConfidenceBadge item={confByKey.keywords} />
        </span>
        <TagInput value={draft.keywords} onChange={(v) => setDraft((d) => ({ ...d, keywords: v }))} placeholder="如：互联网，回车添加" />
      </div>
    </Modal>
  )
}

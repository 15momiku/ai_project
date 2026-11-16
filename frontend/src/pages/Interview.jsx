import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useLocation, useNavigate, useParams } from 'react-router-dom'
import Avatar, { AvatarBoundary } from '../components/Avatar'
import SubtitleBar from '../components/SubtitleBar'
import ProgressDots from '../components/ProgressDots'
import SpeechFeedbackBar from '../components/SpeechFeedbackBar'
import DeviceCheck from '../components/DeviceCheck'
import LoginRequired from '../components/LoginRequired'
import {
  IconBulb,
  IconCheck,
  IconCheckCircle,
  IconInfo,
  IconKeyboard,
  IconMic,
  IconReplay,
  IconTarget,
  IconVolume,
  IconVolumeOff,
  IconWarn,
} from '../components/icons'
import {
  getInterviewState,
  getMediaVoices,
  interviewAnswer,
  interviewSkip,
  loadLocal,
  notify,
  saveLocal,
  saveSpeechMetrics,
} from '../api/client'
import useSpeech from '../hooks/useSpeech'
import useRecorder from '../hooks/useRecorder'
import useAnswerRecorder from '../hooks/useAnswerRecorder'
import { useWakeLock } from '../hooks/useVisibility'
import { useAuth } from '../auth/AuthContext'
import { saveClip } from '../storage/recordings'
import './interview.css'

const fmtClock = (s) => {
  const sec = Math.max(0, Math.floor(s || 0))
  return `${String(Math.floor(sec / 60)).padStart(2, '0')}:${String(sec % 60).padStart(2, '0')}`
}

/**
 * 去掉题面里的括号标注（如「（考察：需求拆解能力）」），只留口语文本。
 * 与后端 `media.strip_annotation` 语义一致：从历史会话恢复时 TranscriptItem 不含
 * speak_text，必须在前端补一次同样的清洗，否则会把标注也念出来。
 */
const stripAnnotation = (text) =>
  String(text || '')
    .replace(/[（(]\s*(?:考察|考察点|注)\s*[:：][^）)]*[）)]/g, '')
    .replace(/[（(][^）)]{0,30}考察[^）)]{0,30}[）)]/g, '')
    .replace(/\s{2,}/g, ' ')
    .trim()

/**
 * 「给我一点思路」的提示卡内容（体验增强 §4）。
 *
 * 只给**框架**，不给参考答案——给答案就等于替用户答题，练不出东西。
 * 因此这里全部是本地静态文案，不发任何请求，也不含题目专属答案。
 */
const HINT_BY_SECTION = {
  自我介绍: '按「学校专业 → 与岗位最相关的 2 段经历 → 一句求职动机」组织，控制在 90 秒内。',
  项目深挖: '按「背景一句话 → 我负责什么 → 第一/第二/第三做了什么 → 一个量化结果」展开。',
  专业技能: '先给一句结论（会到什么程度），再用一个真实项目说明你怎么用、遇到过什么问题。',
  场景题: '先拆问题（目标/约束/可动用的资源），再给方案，最后说怎么验证方案有效。',
  行为面试: '用 STAR 讲一件真事：背景 → 我的任务 → 我的动作 → 结果与复盘。',
  反问环节: '问「业务方向 / 团队分工 / 新人成长」这类能体现你做过功课的问题，各备 1 条。',
}

const STAR_STEPS = [
  { name: '背景', tip: '一句话交代处境与目标，别铺垫太长' },
  { name: '任务', tip: '你个人具体负责哪一部分' },
  { name: '行动', tip: '用「第一/第二/第三」讲清你做了什么' },
  { name: '结果', tip: '给一个数字，没有数字就说明影响面' },
]

/** 当前题草稿的 sessionStorage key（音频不落盘，只存文字草稿） */
const draftKey = (sessionId, index) => `aic_draft_${sessionId}_${index}`

/** 读取草稿：隐私模式下 sessionStorage 不可用，任何异常都当作没有草稿 */
const draftOf = (sessionId, index) => {
  if (!sessionId || !index) return ''
  try {
    return sessionStorage.getItem(draftKey(sessionId, index)) || ''
  } catch {
    return ''
  }
}

/** 写入/清除草稿（空文本即清除，避免留下无意义的空键） */
const saveDraft = (sessionId, index, text) => {
  if (!sessionId || !index) return
  try {
    if (text && text.trim()) sessionStorage.setItem(draftKey(sessionId, index), text)
    else sessionStorage.removeItem(draftKey(sessionId, index))
  } catch {
    /* 隐私模式下忽略 */
  }
}

/**
 * 面试进行中页面（产品最重要的页面）
 *
 * 流程：opening 播放 → 解锁「开始回答」→ 用户作答 → POST answer
 *      → 播放 next_turn → 直到 is_finished=true → 自动跳转 /report/{sessionId}
 */
export default function Interview() {
  const { sessionId } = useParams()
  const nav = useNavigate()
  const location = useLocation()
  // 登录态：游客的一切实录/记录都不保存，面试页的可用能力也随之收敛
  const { user, isGuest, loading: authLoading } = useAuth()

  /* ---- 会话信息 ---- */
  const [session, setSession] = useState({
    jobTitle: '',
    companyName: '',
    total: 9,
    mode: 'voice',
    interviewer: 'female',
  })
  const [turn, setTurn] = useState(null)
  const [phase, setPhase] = useState('loading') // loading | speaking | ready | answering | review | submitting | finished | error
  const [elapsed, setElapsed] = useState(0)
  const [ansSec, setAnsSec] = useState(0)
  const [answerText, setAnswerText] = useState('')
  const [mode, setMode] = useState('voice')
  const [muted, setMuted] = useState(false)
  const [banner, setBanner] = useState('')
  const [loadError, setLoadError] = useState('')

  /* ---- 体验增强 §2 / §4：进度、重录次数、提示使用、口播指标 ---- */
  const [prog, setProg] = useState(null)
  const [retakeCount, setRetakeCount] = useState(0)
  const [hintUsed, setHintUsed] = useState(false)
  const [showHint, setShowHint] = useState(false)
  const [askSkip, setAskSkip] = useState(false)
  const [devOpen, setDevOpen] = useState(false)
  const [devResult, setDevResult] = useState(null)

  /* ---- 屏幕常亮 + 焦点管理（阶段 4 横向规范） ----
     语音作答要连续说 60–120 秒，中途系统息屏会直接打断录制/语音播放；
     Safari 在息屏时还会挂起 AudioContext，切回来第一句常常丢失。
     Wake Lock 拿不到（老浏览器 / 省电模式 / 非 HTTPS）时给一次温和提示，
     引导用户去系统设置关掉自动锁屏——绝不因此阻断流程。 */
  const [lockHint, setLockHint] = useState(false)
  const lockHintShownRef = useRef(false)
  const questionRef = useRef(null) // 题目/字幕区：进入 ready 时把焦点交给它
  const submitRef = useRef(null) // 「提交并继续」：复核态的第一步动作
  const autoFocusDoneRef = useRef(false)

  const speech = useSpeech()
  // 后端报告 asr_provider=aliyun 时走云端识别，否则走浏览器 Web Speech API（browser，兜底）
  const [asrProvider, setAsrProvider] = useState('browser')
  const [ttsProvider, setTtsProvider] = useState('')
  const rec = useRecorder(asrProvider)
  const bootRef = useRef(false)
  const playingTurnRef = useRef('')
  const ansTimerRef = useRef(0)
  // 音色必须走 ref：playTurn 是 useCallback，bootstrap 只会捕获首次渲染的闭包，
  // 若直接依赖 session.interviewer，选「男声」时第 1 题仍会用默认女声合成。
  const interviewerRef = useRef('female')

  /* ---- 答题录音（本机保存，供「我的面试 · 录音回放」）----
     与 ASR 分开：ASR 把语音转成文字后就把音频丢了，这里把原始音频留下来。
     **只在登录模式启用**——游客的数据一律不保存，音频同理（游客的「我的面试」也打不开）。
     音频只进浏览器 IndexedDB，不上传服务端：项目的隐私铁律是「音频不落盘、不出本机」。 */
  const answerRec = useAnswerRecorder()
  const canRecord = !authLoading && !isGuest && answerRec.supported

  /** 起录（只在该录的时候录，失败静默：回放是增强功能，不该打断面试） */
  const startClip = useCallback(() => {
    if (!canRecord) return
    answerRec.start()
  }, [canRecord, answerRec])

  /** 结束录音并落本机（失败不影响面试） */
  const persistClip = useCallback(async () => {
    if (!canRecord) return
    const out = await answerRec.stop()
    const uid = user?.user_id || ''
    if (!out?.blob || !uid || !sessionId) return
    try {
      await saveClip({
        userId: uid,
        sessionId,
        index: Number(turn?.index) || 0,
        section: turn?.section || '',
        seconds: out.seconds,
        blob: out.blob,
      })
    } catch {
      /* 本机存不下（配额 / 隐私模式）不影响面试主流程 */
    }
  }, [canRecord, answerRec, user?.user_id, sessionId, turn?.index, turn?.section])

  /** 统一写入会话信息：同时更新 ref，保证同一 tick 内播放用的是最新音色 */
  const applySession = useCallback((next) => {
    // 只有显式传了 interviewer 才改音色，否则增量更新（如只更新 total）会把音色打回默认值
    if (next && next.interviewer) interviewerRef.current = next.interviewer
    setSession((s) => ({ ...s, ...next }))
  }, [])

  /* ---- 会话累计时长：以后端累计值为基准 + 本地增量 ----
     只用本地计时器会在刷新后归零（中断恢复场景），只读后端值又不会秒级跳动，
     因此两者相加：基准随每次 responses 同步，界面每秒重算。 */
  const progBaseRef = useRef({ seconds: 0, at: Date.now() })

  const syncProg = useCallback((p) => {
    if (!p || typeof p !== 'object') return
    setProg(p)
    progBaseRef.current = {
      seconds: Number(p.elapsed_seconds) || 0,
      at: Date.now(),
    }
  }, [])

  useEffect(() => {
    const t = setInterval(() => {
      const base = progBaseRef.current
      setElapsed(base.seconds + Math.floor((Date.now() - base.at) / 1000))
    }, 1000)
    return () => clearInterval(t)
  }, [])

  /* ---- 语音降级提示（文档 §4.7） ---- */
  useEffect(() => {
    const err = speech.error
    if (!err) return
    // 用了浏览器语音兜底 ≠ 故障：面试官照样出声，只说明后端那一路不可用，直接展示原因
    if (err.includes('浏览器语音')) {
      setBanner(err)
    } else if (err.includes('拦截')) {
      setBanner(err)
    } else if (err.includes('失败') || err.includes('不可用')) {
      setBanner('语音加载失败，已切换文字模式（字幕将正常显示）')
    } else {
      setBanner(err)
    }
  }, [speech.error])

  /* ---- 数字人不可用时不阻断 ---- */
  const [avatarOff, setAvatarOff] = useState(false)

  /* ---- 屏幕常亮：从加载完成到结束前全程保持（切后台回来由钩子自动重申请） ---- */
  const keepAwake = phase !== 'loading' && phase !== 'error' && phase !== 'finished'
  const wakeLock = useWakeLock(keepAwake)

  /* Wake Lock 拿不到时，在「真正开始说话」后才提示一次。
     留 4 秒宽限期：申请是异步的，立刻提示会误伤首次申请的几百毫秒。
     文字模式下用户一直在敲键盘，系统不会息屏，因此不打扰。 */
  useEffect(() => {
    if (phase !== 'answering' || mode !== 'voice') return undefined
    if (wakeLock.active || lockHintShownRef.current) return undefined
    const t = setTimeout(() => {
      if (lockHintShownRef.current) return
      lockHintShownRef.current = true
      setLockHint(true)
    }, 4000)
    return () => clearTimeout(t)
  }, [phase, mode, wakeLock.active])

  /* ---- 焦点管理：让键盘/读屏用户能不碰鼠标走完一题 ----
     1) 题目就绪时焦点落到题目区（仅当焦点还在 body —— 即刚进入页面，
        不抢正在输入文字的用户的焦点）；preventScroll 避免移动端把页面
        拽到题目处、跳过正在朗读的数字人。
     2) 语音作答结束进入复核态时，焦点落到「提交并继续」，下一步不言自明。 */
  const prevPhaseRef = useRef(phase)
  useEffect(() => {
    const prev = prevPhaseRef.current
    prevPhaseRef.current = phase
    if (prev === phase) return
    if (phase === 'ready' && !autoFocusDoneRef.current) {
      autoFocusDoneRef.current = true
      const ae = typeof document !== 'undefined' ? document.activeElement : null
      if (!ae || ae === document.body) questionRef.current?.focus?.({ preventScroll: true })
    }
    if (phase === 'review' && prev === 'answering') {
      submitRef.current?.focus?.({ preventScroll: true })
    }
  }, [phase])

  /* ---- 阶段播报：按钮文案变化、spinner 这类「无声的异步状态」要说给读屏听 ---- */
  const phaseLabel = useMemo(() => {
    switch (phase) {
      case 'loading':
        return '正在准备面试。'
      case 'speaking':
        return '面试官正在提问，请听题。'
      case 'ready':
        return '提问已结束，可以开始回答。'
      case 'answering':
        return '正在记录你的回答。'
      case 'review':
        return '回答已结束，请确认内容后提交，也可以重录。'
      case 'submitting':
        return '正在提交并生成下一题。'
      case 'finished':
        return '面试已结束。'
      default:
        return ''
    }
  }, [phase])

  /* ---- 播放一个面试官 Turn ---- */
  const playTurn = useCallback(
    async (t) => {
      if (!t) return
      const key = t.turn_id || `${t.index}-${String(t.content || '').slice(0, 12)}`
      // 同一题并发触发两次播放（重连、双击）时直接忽略，避免面试官把同一句念两遍
      if (key && playingTurnRef.current === key) return
      playingTurnRef.current = key
      setTurn(t)
      try {
        if (muted) {
          setPhase('ready')
          return
        }
        setPhase('speaking')
        try {
          await speech.speak(t.speak_text || t.content || '', interviewerRef.current)
        } catch {
          /* speak 内部已兜底，异常不阻断 */
        }
        setPhase((p) => (p === 'speaking' ? 'ready' : p))
      } finally {
        if (playingTurnRef.current === key) playingTurnRef.current = ''
      }
    },
    [muted, speech]
  )

  /* ---- 初始化：优先用 start 返回的数据，其次从后端恢复 ---- */
  useEffect(() => {
    // 游客不初始化：面试仅登录用户可用，否则这里会打一个必然 401 的请求
    if (authLoading || isGuest) return
    if (bootRef.current) return
    bootRef.current = true

    const bootstrap = async () => {
      const startData = location.state?.start
      if (startData?.session_id) {
        applySession({
          jobTitle: startData.job_title || '',
          companyName: startData.company_name || '',
          total: startData.total_questions || 9,
          mode: startData.mode || 'voice',
          interviewer: startData.interviewer || 'female',
        })
        setMode(startData.mode === 'text' ? 'text' : startData.mode || 'voice')
        await playTurn(startData.opening)
        return
      }
      try {
        const st = await getInterviewState(sessionId)
        applySession({
          jobTitle: st?.job_title || '',
          companyName: st?.company_name || '',
          total: st?.total_questions || 9,
          mode: st?.mode || 'voice',
          interviewer: st?.interviewer || 'female',
        })
        setMode(st?.mode === 'text' ? 'text' : st?.mode || 'voice')
        // 体验增强 §2：刷新/重进后进度条与「重录·提示」计数从后端恢复
        syncProg(st?.progress)
        // finished / aborted 都是终态：aborted 是后端判定「长时间无操作」自动收敛的会话，
        // 此时 answer/skip 已被 409 拒绝，继续留在面试页只会对着一道答不完的题 → 直接去报告页。
        if (st?.status === 'finished' || st?.status === 'aborted') {
          if (st.status === 'aborted') {
            // 文案必须说清「数据没丢」：只说「已自动结束」会让用户以为作答内容被清空了
            notify('这场面试因长时间无操作已自动结束，作答内容已保留，报告可正常生成', 'warn', 4000)
          }
          nav(`/report/${sessionId}`, { replace: true })
          return
        }
        const dialogue = Array.isArray(st?.dialogue) ? st.dialogue : []
        const last = [...dialogue].reverse().find((d) => d.role === 'interviewer')
        if (last) {
          // 恢复该题的本地草稿（只存文字，音频不落盘）：刷新不丢已答内容
          const saved = draftOf(sessionId, Number(last.index) || 0)
          if (saved) setAnswerText(saved)
          // 拔掉括号标注再朗读：TranscriptItem 不含 speak_text，
          // 直接用 content 会把「（考察：xxx）」这类标注一起念出来（契约 §1.3）。
          await playTurn({ ...last, speak_text: stripAnnotation(last.content) })
        } else {
          setPhase('error')
          setLoadError('未找到面试题目，请重新开始一场面试。')
        }
      } catch (err) {
        setPhase('error')
        setLoadError(err?.message || '面试会话加载失败')
      }
    }
    bootstrap()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sessionId, authLoading, isGuest])

  /* ---- 查询后端实际生效的语音提供方：ASR 推出阿里云 AppKey 就自动切云端识别 ----
     同一个响应里的 `provider` 就是 TTS 的**实际生效**提供方，交给设备自检展示，
     因此自检结果永远与后端真实链路一致（不会出现「配了阿里云却显示阿里云」）。 */
  useEffect(() => {
    let alive = true
    getMediaVoices()
      .then((m) => {
        if (!alive) return
        if (m?.asr_provider) setAsrProvider(m.asr_provider)
        if (m?.provider) setTtsProvider(m.provider)
      })
      .catch(() => {
        /* 查询失败维持 browser 兜底 */
      })
    return () => {
      alive = false
    }
  }, [])

  /* ---- 首次进入语音面试且没做过自检时，用一条可关闭的提示引导（而不是强制弹窗）----
     产品目标：不允许出现「答到第 3 题才发现麦克风没声音」。 */
  const [askSelfCheck, setAskSelfCheck] = useState(false)
  useEffect(() => {
    if (mode !== 'voice' || devResult) return
    if (loadLocal('aic_device_checked', false)) return
    setAskSelfCheck(true)
  }, [mode, devResult])

  /* ---- 语音识别不支持时引导切文字模式 ---- */
  useEffect(() => {
    if (!rec.supported && mode === 'voice') {
      setMode('text')
      notify('当前浏览器不支持语音识别，已切换为文字模式', 'warn', 3200)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [rec.supported])

  /* ---- 回答计时 ---- */
  const stopAnsTimer = () => {
    clearInterval(ansTimerRef.current)
    ansTimerRef.current = 0
  }
  useEffect(() => () => stopAnsTimer(), [])

  const beginTimer = () => {
    stopAnsTimer()
    setAnsSec(0)
    ansTimerRef.current = setInterval(() => setAnsSec((v) => v + 1), 1000)
  }

  /* ---- 开始回答 ---- */
  const onStartAnswer = () => {
    speech.stop()
    rec.reset()
    setAnswerText('')
    setPhase('answering')
    // 这里**不能**再出现 `setMetrics(null)` 这类残留的 setter：
    // 口播指标已经由 `SpeechFeedbackBar` 自己按 props 计算（见下方组件），页面不再持有它。
    // 之前那行漏声明的 setMetrics 会抛 ReferenceError，把它后面的
    // beginTimer() / rec.start() / startClip() 全部中断——界面照样显示「正在记录」，
    // 但计时不动、云端识别与答题录音从未启动，用户看到的就是「说了话却没录进去」。
    beginTimer()
    if (mode === 'voice' && rec.supported) {
      rec.start()
    }
    // 语音作答时同时留一份本机录音（登录模式下），供事后回放
    if (mode === 'voice') startClip()
  }

  /**
   * 结束录音 → 进入「复核」态，而不是直接提交（体验增强 §4）。
   *
   * 直接提交会毁掉整场：用户说错一句没有退路。复核态给出
   * [提交并继续] [重录] [给我一点思路] [跳过本题]。
   */
  const onStopAnswer = async () => {
    if (phase !== 'answering') return
    stopAnsTimer()
    // 录音与识别同时收尾：识别结果用于评分，录音用于回放（本机）
    const clip = persistClip()
    // rec.stop() 会把尚未定稿的识别结果并入转写并返回完整文本（云端为异步 Promise，
    // 浏览器为同步字符串，await 两种都兼容），否则用户说完立刻点「结束回答」会丢掉最后一句。
    const recognized = (await rec.stop()) || ''
    await clip
    const candidates = [recognized, answerText, rec.transcript].filter((x) => x && x.trim())
    // 取最长的一条：answerText 可能还是上一帧的旧值，而 recognized 已包含最终结果
    const text = (candidates.sort((a, b) => b.trim().length - a.trim().length)[0] || '').trim()
    if (!text) {
      notify('还没有回答内容：请说话或用键盘输入，也可以点「跳过本题」', 'warn', 3200)
      setPhase('ready')
      return
    }
    const seconds = ansSec > 0 ? ansSec : Math.max(1, Math.round(text.length / 4.5))
    setAnswerText(text)
    rec.setTranscript(text)
    saveDraft(sessionId, turn?.index, text)
    setPhase('review')
  }

  /* ---- 提交并继续 ---- */
  const onSubmitAnswer = async () => {
    if (phase !== 'answering' && phase !== 'review') return
    const text = (answerText || rec.transcript || '').trim()
    if (!text) {
      notify('还没有回答内容：请说话或用键盘输入，也可以点「跳过本题」', 'warn', 3200)
      return
    }
    // answer_seconds 必须 > 0：优先用实际计时，其次按字数 / 4.5 估算
    const seconds = ansSec > 0 ? ansSec : Math.max(1, Math.round(text.length / 4.5))
    setPhase('submitting')

    // 口播指标只上报、不计分，失败也不打扰用户（观测数据不该阻断答题）。
    // 只发文本 + 时长：语速/填充词由服务端用 constants 里的同一套阈值重算，
    // 避免前端算法漂移导致「前端说偏快、报告说正常」。
    if (mode === 'voice') {
      saveSpeechMetrics(sessionId, {
        question_index: Number(turn?.index) || 0,
        answer_text: text,
        duration_seconds: seconds,
      }).catch(() => {
        /* 静默：口播指标是增强信息 */
      })
    }

    try {
      const data = await interviewAnswer(sessionId, {
        question_id: turn?.turn_id || '',
        answer_text: text,
        answer_seconds: seconds,
        input_mode: mode,
        // 诚实披露：重录与依赖提示都会写进报告，让用户看见自己的犹豫点
        retake: retakeCount > 0,
        hint_used: hintUsed,
      })
      await afterStep(data)
    } catch {
      setPhase('ready')
    }
  }

  /* ---- 重录：覆盖当前题答案，不计惩罚（报告里如实标注） ---- */
  const onRetake = () => {
    if (phase !== 'review') return
    speech.stop()
    rec.reset()
    // 重录会覆盖本题：旧的录音片段直接丢弃，避免同一题留下两段音频
    answerRec.cancel()
    setAnswerText('')
    setRetakeCount((v) => v + 1)
    saveDraft(sessionId, turn?.index, '')
    setPhase('answering')
    beginTimer()
    if (mode === 'voice' && rec.supported) rec.start()
    if (mode === 'voice') startClip()
    notify('已重新开始本题，之前的草稿已清除', 'info', 2600)
  }

  /* ---- 思路提示：只给 STAR 框架，不给参考答案 ---- */
  const onToggleHint = () => {
    const next = !showHint
    setShowHint(next)
    if (next && !hintUsed) setHintUsed(true)
  }

  /* ---- 跳过本题 ---- */
  const onSkip = async () => {
    stopAnsTimer()
    rec.stop()
    // 跳过的题不保留录音：回放列表里出现一段没有答案的音频只会让人困惑
    answerRec.cancel()
    setAskSkip(false)
    setPhase('submitting')
    try {
      const data = await interviewSkip(sessionId, turn?.turn_id || '')
      await afterStep(data)
    } catch {
      setPhase('ready')
    }
  }

  /* ---- 处理一步推进的结果 ---- */
  const afterStep = async (data) => {
    const finishedIndex = Number(turn?.index) || 0
    setAnswerText('')
    rec.reset()
    setAnsSec(0)
    setRetakeCount(0)
    setHintUsed(false)
    setShowHint(false)
    setAskSkip(false)
    // 本题已提交：草稿不再需要，留着会在下次进入同题号时误当成新草稿
    saveDraft(sessionId, finishedIndex, '')
    // 体验增强 §2：每次推进都用后端的最新进度刷新进度条
    syncProg(data?.progress_info)
    if (data?.is_finished) {
      setPhase('finished')
      await playClosing()
      nav(`/report/${sessionId}`, { replace: true })
      return
    }
    if (data?.next_turn) {
      const next = data.next_turn
      if (next.total) {
        applySession({ total: next.total })
      }
      await playTurn(next)
    } else {
      setPhase('ready')
    }
  }

  /* ---- 播放结束语（结束后自动生成报告） ---- */
  const playClosing = async () => {
    try {
      const st = await getInterviewState(sessionId)
      const dialogue = Array.isArray(st?.dialogue) ? st.dialogue : []
      const closing = [...dialogue].reverse().find((d) => d.section === '结束语' && d.role === 'interviewer')
      if (closing && !muted) {
        setTurn(closing)
        await Promise.race([
          speech.speak(stripAnnotation(closing.content) || closing.content || '', interviewerRef.current),
          new Promise((r) => setTimeout(r, 12000)),
        ])
      }
    } catch {
      /* 结束语获取失败不影响跳转 */
    }
  }

  /* ---- 退出：二次确认由 ProgressDots 负责，这里只做清理与跳转 ----
     刻意**不**结束会话：确认退出后用户仍可从首页回到本场继续作答，
     报告也能随时生成（后端只要求会话存在）。 */
  const onExit = () => {
    speech.stop()
    rec.stop()
    saveDraft(sessionId, turn?.index, answerText)
    notify('本场面试已保留，可从首页回到「继续面试」接着答', 'info', 3600)
    nav('/')
  }

  /* ---- 静音开关 ---- */
  const onToggleMute = () => {
    const next = !muted
    setMuted(next)
    if (next) {
      speech.stop()
      setPhase((p) => (p === 'speaking' ? 'ready' : p))
      notify('已静音：面试官语音关闭，字幕正常显示', 'info', 2200)
    } else {
      notify('已取消静音', 'info', 1800)
    }
  }

  /* ---- 模式切换（语音 / 文字） ---- */
  const onSwitchMode = () => {
    const next = mode === 'voice' ? 'text' : 'voice'
    if (next === 'voice' && !rec.supported) {
      notify('当前浏览器不支持语音识别，无法切到语音模式', 'warn')
      return
    }
    setMode(next)
    if (phase === 'answering') {
      if (next === 'voice') {
        rec.start()
        startClip()
      } else {
        rec.stop()
        // 切到文字回答前把已录的这段存下来（用户确实作答过，不该凭空丢掉）
        persistClip()
      }
    }
    notify(next === 'voice' ? '已切换为语音回答' : '已切换为文字回答', 'info', 1800)
  }

  /* ---- 转写文本：ASR 结果写入可编辑 textarea ---- */
  const shownTranscript = answerText || rec.transcript
  const onEditTranscript = (v) => {
    setAnswerText(v)
    rec.setTranscript(v)
  }
  useEffect(() => {
    if (rec.transcript) setAnswerText(rec.transcript)
  }, [rec.transcript])

  /**
   * 进度条的显示模型（体验增强 §2）。
   *
   * 优先用后端的结构化 progress；首屏还没拿到任何响应时退化为本地推导，
   * 保证进度条不会先空白再跳出来。结束语 / 开场白 index=0 不是「第 1 题」，
   * 直接把 0 夹成 1 会让进度在收尾时突然退回 1/N。
   */
  const progressModel = useMemo(() => {
    const total = Number(prog?.total) || turn?.total || session.total || 9
    const idx = Number(prog?.index) || Number(turn?.index) || 0
    const isWrapUp = !idx || turn?.section === '结束语'
    return {
      index: isWrapUp ? total : idx,
      total,
      answered: Number(prog?.answered) || 0,
      stage: prog?.stage || turn?.section || '',
      estimatedRemaining: Array.isArray(prog?.estimated_remaining_seconds)
        ? prog.estimated_remaining_seconds
        : [],
      source: prog?.source || 'estimate',
      canResume: prog?.can_resume !== false,
      idleTimeoutHours: Number(prog?.idle_timeout_hours) || 0,
    }
  }, [prog, turn, session.total])

  const speakingNow = phase === 'speaking' && speech.speaking

  /* ---- 游客模式：面试仅登录用户可用 ----
     放在所有 hooks 之后（hooks 必须无条件执行），后端接口同样返回 401。 */
  if (!authLoading && isGuest) {
    return <LoginRequired what="模拟面试" points={['完整跑完一场模拟面试并拿到六维评分报告']} />
  }

  /* ---- 加载态 / 错误态 ---- */
  if (phase === 'loading') {
    return (
      <div className="container container--flush">
        <div className="card mt-24">
          <div className="skeleton sk-line sk-line--40" />
          <div className="skeleton sk-line" />
          <div className="skeleton" style={{ height: 220, marginTop: 12 }} />
          <div className="skeleton sk-line mt-16" />
        </div>
        <div className="center muted mt-24">正在连接面试间…</div>
      </div>
    )
  }

  if (phase === 'error') {
    return (
      <div className="container container--flush">
        <div className="card mt-24">
          <div className="empty">
            <IconInfo size={28} style={{ color: 'var(--text-mute)' }} />
            <div className="empty-title">面试会话加载失败</div>
            <div className="empty-desc">{loadError}</div>
            <div className="row gap-12 mt-16" style={{ justifyContent: 'center' }}>
              <button className="btn btn--primary" onClick={() => nav('/setup')}>
                重新开始面试
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

  return (
    <div className="interview-page">
      {/* ================= 顶部状态栏 =================
          整条顶栏只有一层 sticky（由 ProgressDots 提供），页面自己的
          AI 标识 / 岗位名 / 开关作为 children 注入第一行 */}
      <ProgressDots
        index={progressModel.index}
        total={progressModel.total}
        answered={progressModel.answered}
        stage={progressModel.stage}
        elapsedSeconds={elapsed}
        estimatedRemaining={progressModel.estimatedRemaining}
        source={progressModel.source}
        canResume={progressModel.canResume}
        idleTimeoutHours={progressModel.idleTimeoutHours}
        onExit={onExit}
      >
        <div className="iv-top-inner">
          <span className="chip chip--danger iv-live-chip" title="当前对话由 AI 生成，非真人面试官">
            <span className="dot dot--live" />
            AI 面试官 · 进行中
          </span>
          <div className="iv-top-right">
            {/* 设备自检常驻状态点：可随时点开重测（体验增强 §3） */}
            <button
              className="btn btn--ghost btn--sm"
              onClick={() => setDevOpen(true)}
              title="重测麦克风与语音（约 30 秒）"
            >
              {devResult ? (
                devResult.ok ? (
                  <IconCheckCircle size={16} />
                ) : (
                  <IconWarn size={16} />
                )
              ) : (
                <IconMic size={16} />
              )}
              <span className="hide-sm">
                {devResult ? (devResult.ok ? '设备已就绪' : '设备待处理') : '设备自检'}
              </span>
            </button>
            <button
              className={`btn btn--sm ${muted ? 'btn--soft' : 'btn--ghost'}`}
              onClick={onToggleMute}
              title="静音开关"
            >
              {muted ? <IconVolumeOff size={16} /> : <IconVolume size={16} />}
              <span className="hide-sm">{muted ? '已静音' : '声音'}</span>
            </button>
          </div>
        </div>
        <div className="iv-title-row">
          <span className="iv-job">{session.jobTitle || '目标岗位'}</span>
          <span className="iv-company">{session.companyName || '目标公司'}</span>
        </div>
      </ProgressDots>

      <div className="container container--flush iv-body">
        {/* 阶段播报：字幕/按钮变化对读屏用户是无声的，这里补一条礼貌播报 */}
        <p className="sr-only" role="status" aria-live="polite" aria-atomic="true">
          {phaseLabel}
        </p>
        {lockHint && (
          <div className="alert alert--info mb-12">
            <IconInfo size={17} />
            <span className="grow">
              当前浏览器拿不到「屏幕常亮」权限，长时间作答时屏幕可能自动熄灭并中断录音。
              建议在系统设置里把自动锁屏时间调长，或答题时偶尔轻触屏幕。
            </span>
            <button className="link-btn" onClick={() => setLockHint(false)}>
              知道了
            </button>
          </div>
        )}
        {askSelfCheck && (
          <div className="alert alert--info mb-12">
            <IconMic size={17} />
            <span className="grow">
              还没测过设备和麦克风。建议先花 30 秒自检，避免答到一半才发现没声音。
            </span>
            <button
              className="btn btn--primary btn--sm"
              onClick={() => {
                setAskSelfCheck(false)
                setDevOpen(true)
              }}
            >
              开始自检
            </button>
            <button
              className="link-btn"
              onClick={() => {
                setAskSelfCheck(false)
                saveLocal('aic_device_checked', true)
              }}
            >
              不再提示
            </button>
          </div>
        )}

        {banner && (
          <div className="alert alert--warn mb-12">
            <IconVolumeOff size={17} />
            <span className="grow">{banner}</span>
            <button
              className="link-btn"
              onClick={() => {
                setBanner('')
                speech.clearError()
              }}
            >
              知道了
            </button>
          </div>
        )}

        <div className="iv-grid">
          {/* ================= 数字人 + 字幕 ================= */}
          <div className="iv-stage">
            <div className="card iv-avatar-card">
              {turn?.is_followup && <span className="iv-followup chip chip--warn">面试官追问</span>}
              {avatarOff ? (
                <div className="empty">
                  <div className="empty-desc">数字人已关闭</div>
                  <button className="btn btn--ghost btn--sm mt-8" onClick={() => setAvatarOff(false)}>
                    重新开启数字人
                  </button>
                </div>
              ) : (
                <AvatarBoundary gender={session.interviewer} speaking={speakingNow} volume={speech.volume}>
                  <Avatar
                    gender={session.interviewer}
                    speaking={speakingNow}
                    volume={speech.volume}
                    muted={muted}
                  />
                </AvatarBoundary>
              )}
              <div className="iv-stage-meta">
                <span className="chip">
                  {session.interviewer === 'male' ? '沉稳男面试官' : '知性女面试官'}
                </span>
                <span className="chip chip--accent">{turn?.section || '面试中'}</span>
                {speech.provider && speech.provider !== 'browser' && (
                  <span className="chip" title="当前语音引擎">
                    {speech.provider === 'aliyun' ? '阿里云' : 'edge-tts'}
                  </span>
                )}
                <span className="iv-stage-links">
                  <button
                    className="link-btn"
                    onClick={() => playTurn(turn)}
                    disabled={phase === 'speaking' || phase === 'answering'}
                  >
                    <IconReplay size={15} /> 重听提问
                  </button>
                  <button className="link-btn" onClick={() => setAvatarOff((v) => !v)}>
                    {avatarOff ? '显示数字人' : '关闭数字人'}
                  </button>
                </span>
              </div>
            </div>

            <div className="iv-talk" ref={questionRef} tabIndex={-1} role="group" aria-label="面试官提问">
              {/* 考察点胶囊 */}
              {turn?.focus && (
                <div className="iv-focus-row">
                  <span className="chip chip--primary">
                    <IconTarget size={14} /> 考察点：{turn.focus}
                  </span>
                  {turn?.is_followup && <span className="chip chip--warn">追问</span>}
                </div>
              )}

              {/* 口播实时反馈：录音态过渡区，只给事实（语速/填充词/时长），不弹窗不打断 */}
              <SpeechFeedbackBar
                active={phase === 'answering' && mode === 'voice'}
                transcript={shownTranscript}
                seconds={ansSec}
              />

              {/* 字幕（打字机：显示进度由音频真实播放位置驱动，与语音同步） */}
              <SubtitleBar
                timeline={speech.sentenceTimeline}
                index={speech.currentSentenceIndex}
                elapsed={speech.playbackTime}
                fallback={turn?.speak_text || turn?.content || ''}
                muted={muted}
              />

              {/* 当前题目原文（带考察标注） */}
              {turn?.content && turn.content !== (turn.speak_text || '') && (
                <details className="iv-question">
                  <summary>查看题目原文</summary>
                  <p>{turn.content}</p>
                </details>
              )}
            </div>
          </div>

          {/* ================= 回答区 ================= */}
          <div className="iv-answer">
            <div className="card">
              <div className="card-head">
                <div className="card-title">
                  <span>你的回答</span>
                  {phase === 'answering' && (
                    <span className="chip chip--ok">
                      <span className="dot dot--live" style={{ background: 'var(--ok)' }} />
                      正在记录
                    </span>
                  )}
                  {/* 诚实披露：重录与提示都摆在用户眼前，不藏在报告里 */}
                  {retakeCount > 0 && (
                    <span className="chip chip--warn" title="重录会如实写进报告，但不扣分">
                      本题重录 {retakeCount} 次
                    </span>
                  )}
                  {hintUsed && (
                    <span className="chip" title="用提示会计入独立作答率，但不扣分">
                      用了思路提示
                    </span>
                  )}
                  {/* 诚实披露：答题录音留在本机浏览器，不上传服务端（游客不录） */}
                  {answerRec.recording && (
                    <span className="chip chip--accent" title="答题录音只保存在这台设备的浏览器里，不会上传服务端">
                      录音仅存本机
                    </span>
                  )}
                </div>
                <span className="chip mono">回答计时 {fmtClock(mode === 'voice' ? Math.max(ansSec, rec.seconds) : ansSec)}</span>
              </div>

              {!rec.supported && mode === 'voice' && (
                <div className="alert alert--warn mb-12">
                  <IconWarn size={17} />
                  <span className="grow">当前浏览器不支持语音识别，请切换为文字模式作答。</span>
                  <button className="link-btn" onClick={onSwitchMode}>
                    切文字模式
                  </button>
                </div>
              )}

              {rec.error && (
                <div className="alert alert--danger mb-12">
                  <IconMic size={17} />
                  <span className="grow">{rec.error}</span>
                  <button className="link-btn" onClick={() => rec.setError('')}>
                    知道了
                  </button>
                </div>
              )}

              {/* 答题录音失败必须说出来：它是「录音回放」的数据来源，而录音只是增强功能，
                  以前这里不显示任何提示，用户只能事后发现「回放里没有声音」却不知道原因。 */}
              {answerRec.error && (
                <div className="alert alert--warn mb-12">
                  <IconMic size={17} />
                  <span className="grow">{answerRec.error}</span>
                  <button className="link-btn" onClick={() => answerRec.setError('')}>
                    知道了
                  </button>
                </div>
              )}

              <textarea
                className="textarea iv-textarea"
                value={shownTranscript}
                placeholder={
                  phase === 'answering'
                    ? mode === 'voice'
                      ? '正在听你说…也可以直接在这里补充/修改文字'
                      : '在这里输入你的回答（建议用「结论 + 第一、第二」结构）'
                    : phase === 'review'
                      ? '确认一下内容，可以直接修改后提交；不满意也可以「重录」'
                      : '点击下方「开始回答」后开始作答'
                }
                disabled={phase !== 'answering' && phase !== 'review'}
                onChange={(e) => onEditTranscript(e.target.value)}
              />

              {mode === 'voice' && phase === 'answering' && rec.interim && (
                <div className="iv-interim">
                  <span className="chip chip--accent">实时识别</span>
                  <span className="mute-2">{rec.interim}</span>
                </div>
              )}

              <div className="iv-answer-meta">
                <span className="mute-2" style={{ fontSize: 12 }}>
                  字数 {(shownTranscript || '').length} · 建议 60-120 秒，用「结论 → 论据 → 结果」结构
                </span>
              </div>

              {/* 「给我一点思路」提示卡：只给 STAR 框架，不给参考答案（体验增强 §4） */}
              {showHint && (
                <div className="iv-hint" role="note">
                  <div className="iv-hint-head">
                    <IconBulb size={16} />
                    <span className="grow">思路提示（不含参考答案，从框架上帮你想清楚）</span>
                    <button className="link-btn" onClick={() => setShowHint(false)}>
                      收起
                    </button>
                  </div>
                  <p className="iv-hint-line">
                    {HINT_BY_SECTION[turn?.section] ||
                      '先说结论，再按「第一、第二、第三」展开，最后给一个结果或复盘。'}
                  </p>
                  <ol className="iv-hint-steps">
                    {STAR_STEPS.map((s) => (
                      <li key={s.name}>
                        <b>{s.name}</b>
                        <span className="mute-2">：{s.tip}</span>
                      </li>
                    ))}
                  </ol>
                  <div className="mute-2" style={{ fontSize: 12 }}>
                    用了提示会在报告里记为「依赖提示」，计入独立作答率——如实记录，不扣分。
                  </div>
                </div>
              )}

              {/* 操作按钮：语音录音结束先进入复核态，避免「说错一句毁掉整场」 */}
              <div className="iv-actions">
                {phase === 'answering' ? (
                  mode === 'voice' ? (
                    <button className="btn btn--ok btn--lg grow" onClick={onStopAnswer}>
                      <IconCheck size={18} /> 结束回答
                    </button>
                  ) : (
                    <button className="btn btn--ok btn--lg grow" onClick={onSubmitAnswer}>
                      <IconCheck size={18} /> 提交并继续
                    </button>
                  )
                ) : phase === 'review' ? (
                  <button ref={submitRef} className="btn btn--primary btn--lg grow" onClick={onSubmitAnswer}>
                    <IconCheck size={18} /> 提交并继续
                  </button>
                ) : (
                  <button
                    className="btn btn--primary btn--lg grow"
                    onClick={onStartAnswer}
                    disabled={phase !== 'ready'}
                  >
                    {phase === 'speaking' ? (
                      '面试官正在提问…'
                    ) : (
                      <>
                        <IconMic size={18} /> 开始回答
                      </>
                    )}
                  </button>
                )}
                {phase === 'review' && (
                  <button className="btn btn--ghost" onClick={onRetake}>
                    <IconReplay size={16} /> 重录
                    {retakeCount > 0 ? `（已重录 ${retakeCount} 次）` : ''}
                  </button>
                )}
                {/* 跳过是半破坏性操作：报告里会记为「未作答」，因此补二次确认 */}
                {askSkip ? (
                  <span className="iv-skip-confirm" role="group" aria-label="确认跳过本题">
                    <span className="mute-2">本题记为未作答？</span>
                    <button className="btn btn--danger btn--sm" onClick={onSkip} disabled={phase === 'submitting'}>
                      确认跳过
                    </button>
                    <button className="btn btn--ghost btn--sm" onClick={() => setAskSkip(false)}>
                      继续作答
                    </button>
                  </span>
                ) : (
                  <button
                    className="btn btn--ghost"
                    onClick={() => setAskSkip(true)}
                    disabled={phase === 'submitting' || phase === 'speaking' || phase === 'finished'}
                  >
                    跳过本题
                  </button>
                )}
              </div>

              <div className="iv-actions iv-actions--sub">
                <button
                  className="btn btn--ghost btn--sm"
                  onClick={onToggleHint}
                  disabled={phase === 'submitting' || phase === 'speaking' || phase === 'finished'}
                >
                  <IconBulb size={16} /> {showHint ? '收起思路' : '给我一点思路'}
                </button>
                <button className="btn btn--ghost btn--sm" onClick={onSwitchMode} disabled={phase === 'speaking'}>
                  {mode === 'voice' ? (
                    <>
                      <IconKeyboard size={16} /> 切换文字模式
                    </>
                  ) : (
                    <>
                      <IconMic size={16} /> 切换语音模式
                    </>
                  )}
                </button>
                <button className={`btn btn--sm ${muted ? 'btn--soft' : 'btn--ghost'}`} onClick={onToggleMute}>
                  {muted ? (
                    <>
                      <IconVolumeOff size={16} /> 取消静音
                    </>
                  ) : (
                    <>
                      <IconVolume size={16} /> 静音面试官
                    </>
                  )}
                </button>
                {phase === 'submitting' && (
                  <span className="row gap-8 mute-2" style={{ fontSize: 12 }} role="status" aria-live="polite">
                    <span className="spinner spinner--primary" aria-hidden="true" />
                    正在提交并生成下一题…
                  </span>
                )}
              </div>
            </div>

            <div className="mute-2 center mt-12" style={{ fontSize: 12 }}>
              面试过程中请尽量保持安静环境；回答会被记录用于生成六维评分报告
            </div>
          </div>
        </div>
      </div>

      {/* 设备自检（体验增强 §3）：可随时点顶部状态点重测，全失败路径都能落到文字模式。
          注意 onDone 在「进入结论页」时就回调（不是关闭时），因此这里只落结果、
          不关弹窗——结论页要留给用户确认；关窗由 onClose 负责。 */}
      <DeviceCheck
        open={devOpen}
        mode={mode}
        ttsProvider={ttsProvider}
        asrProvider={asrProvider}
        onClose={() => setDevOpen(false)}
        onDone={(result) => {
          setDevResult(result)
          saveLocal('aic_device_checked', true)
          if (result?.textMode && mode === 'voice') {
            setMode('text')
            notify('自检建议使用文字模式，已为你切换（流程可完整走完）', 'warn', 4000)
          } else {
            notify(result?.summary || '设备自检完成', result?.ok ? 'success' : 'warn', 3200)
          }
        }}
      />
    </div>
  )
}

import { useCallback, useEffect, useId, useMemo, useRef, useState } from 'react'
import {
  IconBulb,
  IconCheck,
  IconCheckCircle,
  IconInfo,
  IconKeyboard,
  IconLock,
  IconMic,
  IconMinus,
  IconPlay,
  IconRefresh,
  IconScanCheck,
  IconVolume,
  IconVolumeOff,
  IconWarn,
} from './icons'
import { ttsSynthesize } from '../api/client'
// 本组件是跨页面复用的独立弹窗：页面级 CSS 由页面自己引入，
// 这里必须自引样式，否则父组件只用 JSX 时弹窗会没有样式。
import '../styles/device-check.css'

/**
 * 面试前 30 秒设备自检（设计文档 §3）
 *
 * 四步顺序检测，任何一步都能跳过，绝不阻塞面试：
 *   1 浏览器能力   AudioContext / SpeechRecognition / MediaRecorder / getUserMedia
 *   2 麦克风授权   提前 getUserMedia，避免面试进行到一半才弹权限框打断用户
 *   3 音量检测     实时 RMS 音量条，「请说一句话」，真的出现峰值才算通过
 *   4 试听语音     用 ttsSynthesize 播一句面试官开场白，用户确认能听见
 *
 * 三条硬规则：
 *  - **末行结论只写实际生效的提供方**：语音识别/面试官语音的标签全部来自 props
 *    （父组件读 `GET /api/health`）与本次实测结果，后端降级到 edge/浏览器时
 *    这一行绝不会还写着「阿里云」。
 *  - **不录音、不落盘**：只用 AnalyserNode 读实时 RMS，用完立刻 stop 音轨 + close AudioContext。
 *  - **状态完备**：每一步都有 等待中 / 通过 / 失败(可重试) / 跳过，不存在点了没反应的按钮。
 *
 * props
 *  - open:         是否显示弹窗（父组件持有，弹窗只负责渲染）
 *  - mode:         'voice' | 'text'，text 时麦克风检测只作可选建议
 *  - ttsProvider:  后端上报的语音合成提供方 'edge' | 'aliyun' | 'browser' | ''
 *  - asrProvider:  后端上报的语音识别提供方 'aliyun' | 'browser' | ''
 *  - onClose:      用户关闭 / 结束自检时调用（X、Esc、点遮罩、点「进入面试」）
 *  - onDone:       自检有结论时**只调用一次**：四步走完自动回调；中途点
 *                  「跳过自检」也立刻回调一条结论。点 X/Esc 关闭属于「放弃」，
 *                  不会回调 onDone，父组件可据此区分「有结论」与「被关掉」。
 *
 * onDone 的 result 形状（父组件依赖，勿改）：
 *   { ok, mic, asr, tts, textMode, summary }
 */

const STEPS = [
  { key: 'cap', label: '浏览器能力' },
  { key: 'mic', label: '麦克风授权' },
  { key: 'level', label: '音量检测' },
  { key: 'tts', label: '试听语音' },
]

const STEP_HEAD = [
  { title: '浏览器能力', desc: '先确认这台设备跑不跑得动语音面试，只用读能力，不动麦克风。' },
  { title: '麦克风授权', desc: '现在就一次性授权，避免面试进行到一半才弹权限框打断你。' },
  { title: '音量检测', desc: '对着麦克风说一句话，我们实时测音量。不录音、不上传。' },
  { title: '试听面试官语音', desc: '听一句面试官开场白，确认你听得清。' },
]

/** 能力探针：key 对应 probeCapabilities() 的返回字段 */
const PROBES = [
  { key: 'audioContext', name: '音频采集 AudioContext', tip: '音量检测与语音录制的基础' },
  { key: 'speechRecognition', name: '语音识别 SpeechRecognition', tip: '浏览器自带识别；后端启用阿里云识别时不依赖它' },
  { key: 'mediaRecorder', name: '录音 MediaRecorder', tip: '语音作答时录一份存在本机（供「录音回放」）；缺它不影响面试与评分' },
  { key: 'getUserMedia', name: '麦克风接口 getUserMedia', tip: '需要 HTTPS 或 localhost 环境' },
]

/** 峰值判定：RMS 超过阈值并连续命中 2 帧，才认为「用户真的说话了」 */
const PEAK_RMS = 0.05
const PEAK_FRAMES = 2
/** 长静音：这段时间一直没有峰值，就提示检查输入设备 / 静音键 */
const SILENCE_MS = 9000
/** 音量条满量程：日常说话 RMS 约 0.05–0.3，除以 0.35 基本能拉满 */
const LEVEL_FULL_RMS = 0.35

/** 试听用的面试官开场白（固定一句，保证每次自检都说得完） */
const TTS_SAMPLE = '你好，我是本次模拟面试的面试官。请先用一分钟做个自我介绍，不用紧张。'

/* 提供方 → 中文标签。只认实际生效的一方，不做任何美化 */
const TTS_LABEL = { edge: 'edge-tts', aliyun: '阿里云语音', browser: '浏览器朗读' }
const ASR_LABEL = { aliyun: '阿里云识别', browser: '浏览器模式' }
const MIC_LABEL = { ok: '✓', silent: '未检测到声音', denied: '未授权', unsupported: '不支持' }

/* 各浏览器手动开麦克风权限的步骤（拒绝授权后展示，尽量给出可照做的路径） */
const AUTH_GUIDES = {
  chrome: {
    label: 'Chrome',
    steps: [
      '点地址栏最左边的「网站信息」图标，打开「网站设置」',
      '把「麦克风」从「询问」或「禁止」改成「允许」，然后刷新本页',
      '仍未生效：macOS「系统设置 → 隐私与安全性 → 麦克风」里勾选 Google Chrome',
    ],
  },
  edge: {
    label: 'Edge',
    steps: [
      '点地址栏右侧的「锁定」图标 →「此网站的权限」',
      '把「麦克风」改成「允许」，然后刷新本页',
      '仍未生效：Windows「设置 → 隐私 → 麦克风」里打开「允许应用访问你的麦克风」',
    ],
  },
  safari: {
    label: 'Safari',
    steps: [
      'Safari 菜单「设置 → 网站 → 麦克风」，找到本站改为「允许」',
      '仍然不行：macOS「系统设置 → 隐私与安全性 → 麦克风」勾选 Safari',
      '改完刷新本页，回到这里点「我已允许，重新授权」',
    ],
  },
  firefox: {
    label: 'Firefox',
    steps: [
      '点地址栏左侧的「锁」图标 →「已阻止」',
      '在「使用麦克风」一栏点「允许」，然后刷新本页',
      '仍未生效：macOS「系统设置 → 隐私与安全性 → 麦克风」勾选 Firefox',
    ],
  },
  other: {
    label: '其他浏览器',
    steps: [
      '在浏览器设置的「网站权限 → 麦克风」里把本站改为「允许」',
      '检查系统层面的麦克风隐私开关是否已打开',
      '刷新本页后回到这里点「我已允许，重新授权」',
    ],
  },
}

/** 粗判当前浏览器（只用于把对应的权限步骤排在第一位） */
function detectBrowser() {
  const ua = typeof navigator !== 'undefined' ? navigator.userAgent || '' : ''
  if (/Edg\//.test(ua)) return 'edge'
  if (/Firefox\//.test(ua)) return 'firefox'
  if (/Chrome\//.test(ua)) return 'chrome'
  if (/Safari\//.test(ua)) return 'safari'
  return 'other'
}

/** 读一遍浏览器能力。只读探针，不申请权限、不碰麦克风 */
function probeCapabilities() {
  const result = {
    audioContext: false,
    speechRecognition: false,
    mediaRecorder: false,
    getUserMedia: false,
  }
  if (typeof window === 'undefined') return result

  const AC = window.AudioContext || window.webkitAudioContext
  if (AC) {
    // 属性存在 ≠ 可用：隐私模式 / 企业策略下 new 会抛错，必须真建一个再关掉
    try {
      const probe = new AC()
      const closing = probe.close?.()
      if (closing && typeof closing.catch === 'function') closing.catch(() => {})
      result.audioContext = true
    } catch {
      result.audioContext = false
    }
  }

  result.speechRecognition = !!(window.SpeechRecognition || window.webkitSpeechRecognition)
  result.mediaRecorder = typeof window.MediaRecorder !== 'undefined'
  result.getUserMedia = !!(navigator.mediaDevices && navigator.mediaDevices.getUserMedia)
  return result
}

export default function DeviceCheck({
  open = false,
  mode = 'voice',
  ttsProvider = '',
  asrProvider = '',
  onClose,
  onDone,
}) {
  const uid = useId()
  const titleId = `${uid}-title`
  const privacyId = `${uid}-privacy`

  /* ---------------- 流程状态 ---------------- */
  const [step, setStep] = useState(0)
  const [phase, setPhase] = useState('running') // running | summary

  /* ---------------- 第 1 步：浏览器能力 ---------------- */
  const [caps, setCaps] = useState(null) // null = 检测中

  /* ---------------- 第 2 步：麦克风授权 ---------------- */
  const [micState, setMicState] = useState('idle') // idle | checking | ok | denied | unsupported | skipped
  const [micMsg, setMicMsg] = useState('')

  /* ---------------- 第 3 步：音量检测 ---------------- */
  const [levelState, setLevelState] = useState('idle') // idle | listening | ok | silent | skipped
  const [level, setLevel] = useState(0) // 0–1，已量化，避免每帧重渲染

  /* ---------------- 第 4 步：试听语音 ---------------- */
  const [ttsState, setTtsState] = useState('idle') // idle | loading | playing | asking | ok | failed | skipped
  const [ttsMsg, setTtsMsg] = useState('')
  const [ttsFallback, setTtsFallback] = useState(false) // 是否已降级到浏览器朗读（必须显式告知）
  const [ttsTriedBrowser, setTtsTriedBrowser] = useState(false)
  const [ttsUsable, setTtsUsable] = useState(null) // null = 未测；false = 两种语音都听不到
  const [effTts, setEffTts] = useState(() =>
    ttsProvider === 'edge' || ttsProvider === 'aliyun' ? ttsProvider : 'browser'
  )

  /* ---------------- refs（资源与一次性回调） ---------------- */
  const dialogRef = useRef(null)
  const streamRef = useRef(null)
  const ctxRef = useRef(null)
  const audioRef = useRef(null)
  const rafRef = useRef(0)
  const levelQRef = useRef(0)
  const emittedRef = useRef(false)
  const restoreRef = useRef(null)
  const micGenRef = useRef(0) // 授权请求代际：跳过/关弹窗后，迟到的授权结果必须丢弃
  const openRef = useRef(open)
  openRef.current = open // 供异步回调判断弹窗是否还开着（与 SubtitleBar 同一写法）

  const backendTts = ttsProvider === 'edge' || ttsProvider === 'aliyun'
  const browserKey = useMemo(() => detectBrowser(), [])

  /* ============================ 资源释放 ============================ */
  /** 释放麦克风：停音轨 + 关 AudioContext。任何路径离开弹窗都必须走到这里 */
  const releaseMic = useCallback(() => {
    cancelAnimationFrame(rafRef.current)
    rafRef.current = 0
    const stream = streamRef.current
    streamRef.current = null
    if (stream) {
      try {
        stream.getTracks().forEach((t) => t.stop())
      } catch {
        /* 忽略：个别浏览器在音轨已结束时抛错 */
      }
    }
    const ctx = ctxRef.current
    ctxRef.current = null
    if (ctx && ctx.state !== 'closed') {
      try {
        ctx.close()
      } catch {
        /* 忽略 */
      }
    }
  }, [])

  /** 停掉正在播放的试听音频与浏览器朗读 */
  const stopAudio = useCallback(() => {
    const audio = audioRef.current
    audioRef.current = null
    if (audio) {
      try {
        audio.pause()
        audio.removeAttribute('src')
        audio.load?.()
      } catch {
        /* 忽略 */
      }
    }
    try {
      window.speechSynthesis?.cancel?.()
    } catch {
      /* 忽略 */
    }
  }, [])

  // 卸载：一定要把麦克风与 AudioContext 收干净，绝不留下「热麦」
  useEffect(
    () => () => {
      releaseMic()
      stopAudio()
    },
    [releaseMic, stopAudio]
  )

  // 弹窗关闭：同样立刻释放（不能等到组件卸载）
  useEffect(() => {
    if (open) return
    releaseMic()
    stopAudio()
  }, [open, releaseMic, stopAudio])

  /* ============================ 打开时复位 + 第 1 步探针 ============================ */
  const runCapabilityCheck = useCallback(async () => {
    setCaps(null)
    const found = probeCapabilities()
    // 让「检测中」真实可见（同步返回会让 loading 一闪而过，反而像没反应）
    await new Promise((resolve) => setTimeout(resolve, 260))
    if (!openRef.current) return
    setCaps(found)
  }, [])

  useEffect(() => {
    if (!open) return
    // 每次打开都从零开始，避免上一次的结论残留
    emittedRef.current = false
    releaseMic()
    stopAudio()
    setStep(0)
    setPhase('running')
    setMicState('idle')
    setMicMsg('')
    setLevelState('idle')
    setLevel(0)
    levelQRef.current = 0
    setTtsState('idle')
    setTtsMsg('')
    setTtsFallback(false)
    setTtsTriedBrowser(false)
    setTtsUsable(null)
    setEffTts(ttsProvider === 'edge' || ttsProvider === 'aliyun' ? ttsProvider : 'browser')
    runCapabilityCheck()
  }, [open, ttsProvider, releaseMic, stopAudio, runCapabilityCheck])

  /* ============================ 焦点管理 ============================ */
  // 打开：记下触发元素（父组件持有），关闭时把焦点还回去
  useEffect(() => {
    if (open) {
      restoreRef.current = typeof document !== 'undefined' ? document.activeElement : null
      return
    }
    const el = restoreRef.current
    restoreRef.current = null
    if (el && typeof el.focus === 'function' && document.contains(el)) el.focus()
  }, [open])

  // 焦点进弹窗：只在「焦点已经跑到弹窗外面」时才拉回来，
  // 这样用户自己在弹窗内 Tab 不会被抢走，而按钮因状态切换被卸载时也不会丢焦点
  useEffect(() => {
    if (!open) return undefined
    const timer = setTimeout(() => {
      const root = dialogRef.current
      if (!root) return
      const active = typeof document !== 'undefined' ? document.activeElement : null
      // 焦点已经落在弹窗内的真实控件上就不要抢；停在弹窗容器本身时继续往下找主操作
      if (active && root.contains(active) && active !== root) return
      const target =
        root.querySelector('[data-autofocus]:not([disabled])') ||
        root.querySelector(
          'button:not([disabled]):not([data-nofocus]), [href], summary, input, select, textarea, [tabindex]:not([tabindex="-1"])'
        ) ||
        root
      target.focus?.()
    }, 40)
    return () => clearTimeout(timer)
  }, [open, step, phase, caps, micState, levelState, ttsState])

  const handleDismiss = useCallback(() => {
    onClose?.()
  }, [onClose])

  // Esc 关闭（键盘用户必须有出口）
  useEffect(() => {
    if (!open) return undefined
    const onKey = (e) => {
      if (e.key === 'Escape') handleDismiss()
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [open, handleDismiss])

  // 弹窗打开时锁住页面滚动：移动端底部抽屉拖动时背景不能跟着滚（关闭后原样还原）
  useEffect(() => {
    if (!open || typeof document === 'undefined') return undefined
    const prev = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    return () => {
      document.body.style.overflow = prev
    }
  }, [open])

  /* ============================ 第 2 步：麦克风授权 ============================ */
  const requestMic = useCallback(async () => {
    const md = typeof navigator !== 'undefined' ? navigator.mediaDevices : null
    if (!md || !md.getUserMedia) {
      setMicState('unsupported')
      setMicMsg('当前环境拿不到麦克风接口（页面需为 HTTPS 或 localhost，或用的是旧版浏览器）。')
      return
    }
    const gen = micGenRef.current + 1
    micGenRef.current = gen
    setMicState('checking')
    setMicMsg('')
    try {
      const stream = await md.getUserMedia({
        audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
      })
      // 关弹窗 / 已点跳过 / 重复申请：把这份迟到的流立刻停掉，别留热麦
      const stale = gen !== micGenRef.current || !openRef.current
      const prev = streamRef.current
      if (stale) {
        stream.getTracks().forEach((t) => t.stop())
        return
      }
      streamRef.current = stream
      if (prev && prev !== stream) prev.getTracks().forEach((t) => t.stop())
      setMicState('ok')
    } catch (err) {
      if (gen !== micGenRef.current) return
      const name = err?.name || ''
      if (name === 'NotAllowedError' || name === 'SecurityError') {
        setMicState('denied')
        setMicMsg('麦克风权限被拒绝。按下面的步骤在浏览器里允许，再回来点「我已允许，重新授权」。')
      } else if (name === 'NotFoundError' || name === 'DevicesNotFoundError' || name === 'OverconstrainedError') {
        setMicState('unsupported')
        setMicMsg('没有找到可用的麦克风设备，请插上耳麦或在系统里选择输入设备。')
      } else {
        setMicState('unsupported')
        setMicMsg(`无法打开麦克风（${name || '未知原因'}）。`)
      }
    }
  }, [])

  // 进入第 2 步自动申请一次授权：语音面试时权限框必须出现在这里，
  // 而不是面试进行到一半把用户打断。文字模式下改为用户主动点按钮。
  useEffect(() => {
    if (!open || step !== 1 || mode === 'text' || micState !== 'idle') return
    requestMic()
  }, [open, step, mode, micState, requestMic])

  /* ============================ 第 3 步：音量检测 ============================ */
  const startLevelCheck = useCallback(() => {
    levelQRef.current = 0
    setLevel(0)
    setLevelState('listening')
  }, [])

  // 只在「第 3 步 + 正在监听」时开麦；一旦通过/静音超时/切步/关弹窗，
  // effect 清理会立刻停表、断音轨、关 AudioContext
  useEffect(() => {
    if (!open || step !== 2 || levelState !== 'listening') return undefined
    let cancelled = false
    let analyser = null
    let source = null
    let buf = null
    let silentTimer = 0
    let peakFrames = 0

    const pass = () => {
      cancelAnimationFrame(rafRef.current)
      rafRef.current = 0
      setLevelState('ok')
      releaseMic() // 一检测到人声就释放麦克风
    }

    const start = async () => {
      const AC = window.AudioContext || window.webkitAudioContext
      if (!AC) {
        setLevelState('silent')
        return
      }
      try {
        // 复用第 2 步已经拿到的流；没有（例如跳过了授权）就再申请一次
        let stream = streamRef.current
        const live = !!stream && stream.getTracks().some((t) => t.readyState === 'live')
        if (!live) {
          stream = await navigator.mediaDevices.getUserMedia({
            audio: { echoCancellation: true, noiseSuppression: true },
          })
          if (cancelled || !openRef.current) {
            stream.getTracks().forEach((t) => t.stop())
            return
          }
          streamRef.current = stream
        }
        if (cancelled) return

        const ctx = new AC()
        ctxRef.current = ctx
        await ctx.resume?.() // 不 resume 时 analyser 读到的永远是 0
        if (cancelled) return

        source = ctx.createMediaStreamSource(stream)
        analyser = ctx.createAnalyser()
        analyser.fftSize = 1024
        analyser.smoothingTimeConstant = 0.55
        // 只接 analyser，不接 destination：不外放、不采集、不落盘
        source.connect(analyser)

        const useFloat = typeof analyser.getFloatTimeDomainData === 'function'
        buf = useFloat ? new Float32Array(analyser.fftSize) : new Uint8Array(analyser.fftSize)

        const loop = () => {
          if (cancelled) return
          let rms = 0
          if (useFloat) {
            analyser.getFloatTimeDomainData(buf)
            let sum = 0
            for (let i = 0; i < buf.length; i += 1) sum += buf[i] * buf[i]
            rms = Math.sqrt(sum / buf.length)
          } else {
            analyser.getByteTimeDomainData(buf)
            let sum = 0
            for (let i = 0; i < buf.length; i += 1) {
              const d = (buf[i] - 128) / 128
              sum += d * d
            }
            rms = Math.sqrt(sum / buf.length)
          }

          // 量化到 2.5%：音量条够顺滑，又不至于每帧都 setState
          const q = Math.round(Math.min(1, rms / LEVEL_FULL_RMS) * 40) / 40
          if (q !== levelQRef.current) {
            levelQRef.current = q
            setLevel(q)
          }

          if (rms > PEAK_RMS) {
            peakFrames += 1
            if (peakFrames >= PEAK_FRAMES) {
              pass()
              return
            }
          } else {
            peakFrames = 0
          }
          rafRef.current = requestAnimationFrame(loop)
        }
        rafRef.current = requestAnimationFrame(loop)

        silentTimer = setTimeout(() => {
          if (cancelled) return
          // 长静音：提示检查设备/静音键（effect 清理会释放麦克风）
          setLevelState('silent')
        }, SILENCE_MS)
      } catch (err) {
        if (cancelled) return
        const name = err?.name || ''
        if (name === 'NotAllowedError' || name === 'SecurityError') {
          setMicState('denied')
          setMicMsg('麦克风权限被拒绝，音量检测无法进行。')
        } else if (name === 'NotFoundError' || name === 'DevicesNotFoundError') {
          setMicState('unsupported')
          setMicMsg('没有找到可用的麦克风设备。')
        }
        setLevelState('silent')
      }
    }

    start()

    return () => {
      cancelled = true
      clearTimeout(silentTimer)
      cancelAnimationFrame(rafRef.current)
      rafRef.current = 0
      try {
        source?.disconnect?.()
      } catch {
        /* 忽略 */
      }
      try {
        analyser?.disconnect?.()
      } catch {
        /* 忽略 */
      }
      releaseMic()
      levelQRef.current = 0
    }
  }, [open, step, levelState, releaseMic])

  /* ============================ 第 4 步：试听语音 ============================ */
  /** 浏览器原生朗读兜底（后端没有音频产出时用） */
  const speakByBrowser = useCallback(
    (text) =>
      new Promise((resolve) => {
        const synth = typeof window !== 'undefined' ? window.speechSynthesis : null
        const Utter = typeof window !== 'undefined' ? window.SpeechSynthesisUtterance : null
        if (!synth || !Utter) {
          resolve(false)
          return
        }
        let settled = false
        let timer = 0
        const done = (ok) => {
          if (settled) return
          settled = true
          clearTimeout(timer)
          resolve(ok)
        }
        const utter = new Utter(text)
        utter.lang = 'zh-CN'
        utter.rate = 1
        utter.pitch = 1
        const voice = (synth.getVoices?.() || []).find((v) => /zh|Chinese/i.test(v.lang || ''))
        if (voice) utter.voice = voice
        utter.onend = () => done(true)
        utter.onerror = () => done(false)
        // 看门狗：部分浏览器 onend 不触发，不能把流程挂死
        timer = setTimeout(() => done(true), Math.min(60000, Math.max(6000, text.length * 260)))
        try {
          synth.cancel()
          synth.speak(utter)
        } catch {
          done(false)
        }
      }),
    []
  )

  /** 播放后端返回的音频，返回是否真的播完 */
  const playAudioUrl = useCallback((url) => {
    return new Promise((resolve) => {
      const audio = new Audio(url)
      audio.preload = 'auto'
      audioRef.current = audio
      let settled = false
      let timer = 0
      const done = (ok) => {
        if (settled) return
        settled = true
        clearTimeout(timer)
        resolve(ok)
      }
      timer = setTimeout(() => done(true), 30000)
      audio.onended = () => done(true)
      audio.onerror = () => done(false)
      try {
        const p = audio.play()
        if (p && typeof p.catch === 'function') p.catch(() => done(false))
      } catch {
        done(false)
      }
    })
  }, [])

  const playSample = useCallback(async () => {
    stopAudio()
    setTtsMsg('')

    /** 降级到浏览器朗读，并把「已降级」这件事写在界面上 */
    const fallbackToBrowser = async (why) => {
      setTtsFallback(true)
      setEffTts('browser')
      setTtsMsg(why)
      setTtsState('playing')
      const ok = await speakByBrowser(TTS_SAMPLE)
      if (ok) {
        setTtsState('asking')
        return
      }
      setTtsUsable(false)
      setTtsState('failed')
      setTtsMsg('后端语音和浏览器朗读都不可用。可以跳过这一步，用文字模式看面试官提问。')
    }

    if (!backendTts) {
      await fallbackToBrowser(
        ttsProvider === 'browser'
          ? '后端当前使用浏览器朗读（未配置云端语音），试听音色与正式面试一致。'
          : '后端未上报可用的语音提供方，已改用浏览器朗读试听；正式结论以汇总行里的实际标签为准。'
      )
      return
    }

    setTtsState('loading')
    let data = null
    try {
      data = await ttsSynthesize(TTS_SAMPLE, 'female')
    } catch {
      data = null
    }
    if (!openRef.current) return

    const url = data && data.provider !== 'browser' && data.audio_url ? data.audio_url : ''
    if (!url) {
      await fallbackToBrowser(
        `后端语音暂不可用（返回 ${data?.provider || '空'}），已自动改用浏览器朗读，音质会有差异。`
      )
      return
    }

    const played = await playAudioUrl(url)
    if (!played) {
      await fallbackToBrowser('试听音频播放失败，已自动改用浏览器朗读。')
      return
    }
    // 真的播完了才记录生效提供方：以后端实际返回为准，而不是 props 的声明
    setEffTts(data.provider === 'aliyun' ? 'aliyun' : 'edge')
    setTtsState('asking')
  }, [backendTts, ttsProvider, stopAudio, speakByBrowser, playAudioUrl])

  const confirmHeard = useCallback(() => {
    setTtsUsable(true)
    setTtsState('ok')
    setPhase('summary')
  }, [])

  const confirmNotHeard = useCallback(async () => {
    if (effTts === 'browser' || ttsTriedBrowser) {
      setTtsUsable(false)
      setTtsState('failed')
      setTtsMsg('两种语音都没听到：先检查系统音量、静音键与输出设备；也可以跳过试听，用文字模式看面试官提问。')
      return
    }
    // 后端音频播了但用户听不到 → 明确降级到浏览器朗读，再说一次
    setTtsTriedBrowser(true)
    setTtsFallback(true)
    setTtsUsable(null)
    setEffTts('browser')
    setTtsMsg('已自动改用浏览器朗读，再听一次（后端语音可能被静音或音轨异常）。')
    setTtsState('playing')
    const ok = await speakByBrowser(TTS_SAMPLE)
    if (ok) {
      setTtsState('asking')
      return
    }
    setTtsUsable(false)
    setTtsState('failed')
    setTtsMsg('浏览器朗读也不可用。可以跳过这一步，用文字模式看面试官提问。')
  }, [effTts, ttsTriedBrowser, speakByBrowser])

  /* ============================ 结论（只写实际生效的提供方） ============================ */
  const srSupported =
    typeof window !== 'undefined' && !!(window.SpeechRecognition || window.webkitSpeechRecognition)

  const asr = useMemo(() => {
    if (asrProvider === 'aliyun') return 'aliyun'
    if (srSupported) return 'browser'
    return 'unsupported'
  }, [asrProvider, srSupported])

  const mic = useMemo(() => {
    if (micState === 'ok') return levelState === 'silent' ? 'silent' : 'ok'
    if (micState === 'denied') return 'denied'
    if (micState === 'unsupported') return 'unsupported'
    // idle / checking / skipped：始终没拿到授权，按「未授权」上报
    return 'denied'
  }, [micState, levelState])

  const ttsOk = ttsUsable !== false
  // 文字模式是全流程兜底：麦克风或语音识别不可用时都必须建议它
  const textMode = mode === 'text' || mic !== 'ok' || asr === 'unsupported' || ttsUsable === false

  const capProblems = useMemo(() => {
    if (!caps) return []
    const list = []
    if (!caps.audioContext) list.push('浏览器不支持 WebAudio（AudioContext）')
    if (!caps.getUserMedia) list.push('拿不到麦克风接口（页面需为 HTTPS 或 localhost）')
    if (!caps.speechRecognition && asrProvider !== 'aliyun') {
      list.push('浏览器不支持语音识别，且后端没有启用云端识别')
    }
    return list
  }, [caps, asrProvider])
  const capState = !caps ? 'checking' : capProblems.length ? 'fail' : 'ok'

  const buildResult = useCallback(
    (over = {}) => {
      const micV = over.mic || mic
      const asrV = over.asr || asr
      const ttsV = over.tts || effTts
      const textV = over.textMode ?? textMode
      const ttsOkV = over.ttsOk ?? ttsOk
      const asrText =
        asrV === 'aliyun' ? ASR_LABEL.aliyun : asrV === 'browser' ? ASR_LABEL.browser : '不可用'
      const ttsText = ttsOkV ? TTS_LABEL[ttsV] || TTS_LABEL.browser : '不可用'
      return {
        // 麦克风或文字模式可用，且语音或文字模式可用 —— 文字模式下降级同样算「能练」
        ok: (micV === 'ok' || textV) && (ttsOkV || textV),
        mic: micV,
        asr: asrV,
        tts: ttsV,
        textMode: textV,
        summary: `麦克风 ${MIC_LABEL[micV] || MIC_LABEL.denied} · 语音识别 ${asrText} · 面试官语音 ${ttsText}`,
      }
    },
    [mic, asr, effTts, textMode, ttsOk]
  )

  const emit = useCallback(
    (result) => {
      if (emittedRef.current) return
      emittedRef.current = true
      onDone?.(result)
    },
    [onDone]
  )

  // 四步走完 → 自动回调一次结论
  useEffect(() => {
    if (!open || phase !== 'summary') return
    emit(buildResult())
  }, [open, phase, emit, buildResult])

  const goNext = useCallback(() => {
    setStep((s) => (s < STEPS.length - 1 ? s + 1 : s))
  }, [])

  /** 单步跳过：麦克风授权（含「还在等权限弹窗」时） */
  const skipMic = useCallback(() => {
    micGenRef.current += 1 // 让还在飞行中的授权结果作废
    setMicState('skipped')
    setMicMsg('')
    goNext()
  }, [goNext])

  /** 单步跳过：音量检测 */
  const skipLevel = useCallback(() => {
    setLevelState('skipped')
    goNext()
  }, [goNext])

  const handleSkipAll = useCallback(() => {
    // 「跳过自检，直接用文字模式」也算一次结清：先给父组件结论，再关闭
    emit(buildResult())
    onClose?.()
  }, [emit, buildResult, onClose])

  if (!open) return null

  /* ============================ 每步的主操作 ============================ */
  let primary = { label: '下一步', onClick: goNext }
  if (step === 0) {
    if (capState === 'checking') primary = { label: '正在检测…', busy: true }
    else if (capState === 'fail') primary = { label: '重新检测', icon: <IconRefresh size={16} />, onClick: runCapabilityCheck }
    else primary = { label: '下一步', onClick: goNext }
  } else if (step === 1) {
    if (micState === 'checking') primary = { label: '等待浏览器授权…', busy: true }
    else if (micState === 'ok') primary = { label: '下一步', onClick: goNext }
    else if (micState === 'denied')
      primary = { label: '我已允许，重新授权', icon: <IconRefresh size={16} />, onClick: requestMic }
    else if (micState === 'unsupported')
      primary = { label: '再试一次', icon: <IconRefresh size={16} />, onClick: requestMic }
    else if (micState === 'skipped') primary = { label: '下一步', onClick: goNext }
    else primary = { label: '授权麦克风', icon: <IconMic size={16} />, onClick: requestMic }
  } else if (step === 2) {
    if (levelState === 'listening') primary = { label: '正在听…请说一句话', busy: true }
    else if (levelState === 'ok') primary = { label: '下一步', onClick: goNext }
    else if (levelState === 'silent')
      primary = { label: '重新检测', icon: <IconRefresh size={16} />, onClick: startLevelCheck }
    else if (levelState === 'skipped') primary = { label: '下一步', onClick: goNext }
    else primary = { label: '开始检测', icon: <IconVolume size={16} />, onClick: startLevelCheck }
  } else if (step === 3) {
    if (ttsState === 'loading') primary = { label: '正在生成语音…', busy: true }
    else if (ttsState === 'playing') primary = { label: '正在播放…', busy: true }
    else if (ttsState === 'asking') primary = { label: '能听到', icon: <IconCheck size={16} />, onClick: confirmHeard }
    else if (ttsState === 'failed')
      primary = { label: '重新播放', icon: <IconRefresh size={16} />, onClick: playSample }
    else if (ttsState === 'skipped') primary = { label: '查看结论', onClick: () => setPhase('summary') }
    else if (ttsState === 'ok') primary = { label: '查看结论', onClick: () => setPhase('summary') }
    else primary = { label: '播放试听', icon: <IconPlay size={16} />, onClick: playSample }
  }

  const levelPct = Math.round(level * 100)
  const levelHint =
    levelState === 'listening'
      ? level > 0.25
        ? '已经听到你的声音了，保持这个音量'
        : level > 0.05
          ? '有声音了，但偏小，可以离麦克风近一点'
          : '还没有听到声音，请对着麦克风说一句话'
      : levelState === 'ok'
        ? '已检测到人声，麦克风正常工作'
        : levelState === 'silent'
          ? '未检测到声音，请检查输入设备/静音键'
          : ''

  const reach = (i) => phase === 'summary' || i < step
  const stepTone = (i) => {
    if (i === 0) return capState === 'fail' ? 'warn' : capState === 'ok' && reach(0) ? 'ok' : 'todo'
    if (i === 1) {
      if (micState === 'ok') return 'ok'
      if (micState === 'denied' || micState === 'unsupported' || micState === 'skipped') return 'warn'
      return 'todo'
    }
    if (i === 2) {
      if (levelState === 'ok') return 'ok'
      if (levelState === 'silent' || levelState === 'skipped') return 'warn'
      return 'todo'
    }
    if (ttsState === 'ok') return 'ok'
    if (ttsState === 'failed' || ttsState === 'skipped') return 'warn'
    return 'todo'
  }

  /* ============================ 分步内容 ============================ */
  let body = null

  if (step === 0) {
    body = (
      <div className="dc-block">
        <ul className="dc-probe-list">
          {PROBES.map((p) => {
            const value = caps ? !!caps[p.key] : null
            let verdict = value === null ? '检测中' : value ? '支持' : '不支持'
            let tone = value === null ? 'wait' : value ? 'ok' : 'bad'
            if (p.key === 'speechRecognition' && value === false && asrProvider === 'aliyun') {
              verdict = '不需要'
              tone = 'ok'
            }
            if (p.key === 'mediaRecorder' && value === false) {
              verdict = '不影响'
              tone = 'ok'
            }
            return (
              <li key={p.key} className={`dc-probe is-${tone}`}>
                <span className="dc-probe-ico">
                  {tone === 'wait' ? (
                    <span className="dc-spin" aria-hidden="true" />
                  ) : tone === 'ok' ? (
                    <IconCheck size={14} />
                  ) : (
                    <IconWarn size={14} />
                  )}
                </span>
                <span className="dc-probe-main">
                  <span className="dc-probe-name">{p.name}</span>
                  <span className="dc-probe-tip">{p.tip}</span>
                </span>
                <span className="dc-probe-val">{verdict}</span>
              </li>
            )
          })}
        </ul>

        {capState === 'ok' && (
          <div className="dc-box dc-box--ok" role="status">
            <IconCheckCircle size={16} />
            <span>设备和浏览器都能跑语音面试，继续下一步拿麦克风权限。</span>
          </div>
        )}

        {capState === 'fail' && (
          <>
            <div className="dc-box dc-box--warn" role="status">
              <IconWarn size={16} />
              <span>有 {capProblems.length} 项能力缺失：{capProblems.join('；')}。</span>
            </div>
            <div className="dc-textmode">
              <div className="dc-textmode-head">
                <IconKeyboard size={18} />
                <span>
                  建议改用 <b className="dc-tm-chip">文字模式</b>
                </span>
              </div>
              <p>
                练习完全不受影响：文字模式下面试官提问会直接显示成文字，你打字作答，
                追问、评分、报告和错题本都照常走完整流程，只是不采集你的语音。
              </p>
            </div>
            <div className="dc-row">
              <button type="button" className="btn btn--ghost btn--sm" onClick={skipMic}>
                <IconKeyboard size={16} />
                继续（用文字模式）
              </button>
              <span className="dc-row-note">也可以换个浏览器（Chrome / Edge / Safari 新版）再试。</span>
            </div>
          </>
        )}
      </div>
    )
  } else if (step === 1) {
    const guide = AUTH_GUIDES[browserKey] || AUTH_GUIDES.other
    const others = Object.keys(AUTH_GUIDES)
      .filter((k) => k !== browserKey)
      .map((k) => AUTH_GUIDES[k])
    body = (
      <div className="dc-block">
        <p className="dc-lead">
          {mode === 'text'
            ? '当前是文字模式，麦克风属于可选：想之后切回语音，就在这里先授权。'
            : '点「授权麦克风」后浏览器会弹出权限框，选「允许」。现在授权，面试中途就不会再被打断。'}
        </p>

        {micState === 'checking' && (
          <div className="dc-box dc-box--wait" role="status">
            <span className="dc-spin" aria-hidden="true" />
            <span>等待浏览器授权…请在弹窗里点「允许」。</span>
          </div>
        )}

        {micState === 'ok' && (
          <div className="dc-box dc-box--ok" role="status">
            <IconCheckCircle size={16} />
            <span>麦克风已授权，设备就绪。下一步测一下实际音量。</span>
          </div>
        )}

        {micState === 'unsupported' && (
          <div className="dc-box dc-box--warn" role="status">
            <IconWarn size={16} />
            <span>{micMsg}</span>
          </div>
        )}

        {micState === 'denied' && (
          <>
            <div className="dc-box dc-box--warn" role="status">
              <IconWarn size={16} />
              <span>{micMsg}</span>
            </div>
            <div className="dc-auth">
              <div className="dc-auth-title">
                <IconInfo size={16} />
                <span>{guide.label} 里这样开麦克风权限</span>
              </div>
              <ol className="dc-auth-list">
                {guide.steps.map((s) => (
                  <li key={s}>{s}</li>
                ))}
              </ol>
              <details className="dc-auth-more">
                <summary>其他浏览器的步骤</summary>
                {others.map((g) => (
                  <div key={g.label} className="dc-auth-other">
                    <div className="dc-auth-other-name">{g.label}</div>
                    <ol className="dc-auth-list">
                      {g.steps.map((s) => (
                        <li key={s}>{s}</li>
                      ))}
                    </ol>
                  </div>
                ))}
              </details>
            </div>
          </>
        )}

        {micState === 'skipped' && (
          <div className="dc-box dc-box--info" role="status">
            <IconInfo size={16} />
            <span>已跳过麦克风授权，这一步按「未授权」计入结论，随时可以回来重新授权。</span>
          </div>
        )}

        {(micState === 'denied' || micState === 'unsupported' || micState === 'checking') && (
          <div className="dc-row">
            <button type="button" className="btn btn--ghost btn--sm" onClick={skipMic}>
              <IconKeyboard size={16} />
              暂不授权，继续检测其他项
            </button>
          </div>
        )}
      </div>
    )
  } else if (step === 2) {
    body = (
      <div className="dc-block">
        <p className="dc-lead">
          {levelState === 'listening'
            ? '请用平常答题的音量说一句话，例如「大家好，我是今天的面试者」。'
            : '点「开始检测」后对着麦克风说一句话，音量条走到阈值以上即通过。'}
        </p>

        <div className={`dc-meter-wrap${levelState === 'listening' ? ' is-live' : ''}`}>
          <div className="dc-meter-head">
            <span className="dc-meter-label">
              <IconVolume size={16} />
              {levelState === 'listening' ? '正在检测音量' : '音量检测'}
            </span>
            <span className="dc-meter-pct">{levelPct}%</span>
          </div>
          <div className="dc-meter" aria-hidden="true">
            <div className="dc-meter-fill" style={{ width: `${levelPct}%` }} />
            <span
              className="dc-meter-mark"
              style={{ left: `${(PEAK_RMS / LEVEL_FULL_RMS) * 100}%` }}
            />
          </div>
          <div className="dc-meter-foot">
            <span>说话阈值</span>
            <span>只在本地实时测音量，不录音、不上传</span>
          </div>
          {/* 音量/静音提示用 role=status：颜色之外还有文字，屏幕阅读器也能播报 */}
          <div className={`dc-meter-hint is-${levelState}`} role="status">
            {levelHint}
          </div>
        </div>

        {levelState === 'silent' && (
          <div className="dc-box dc-box--warn">
            <IconWarn size={16} />
            <span>
              未检测到声音，请检查输入设备/静音键：确认系统输入设备选的是当前麦克风、
              没有按到静音键、也没有把麦克风挡在静音位。
            </span>
          </div>
        )}

        {levelState === 'ok' && (
          <div className="dc-box dc-box--ok" role="status">
            <IconCheckCircle size={16} />
            <span>已检测到人声，麦克风采集正常，麦克风已经立刻释放。</span>
          </div>
        )}

        {levelState !== 'ok' && levelState !== 'skipped' && (
          <div className="dc-row">
            <button type="button" className="btn btn--ghost btn--sm" onClick={skipLevel}>
              <IconKeyboard size={16} />
              {levelState === 'silent' ? '仍然继续（可能只是环境安静）' : '跳过音量检测'}
            </button>
          </div>
        )}
      </div>
    )
  } else {
    body = (
      <div className="dc-block">
        <p className="dc-lead">
          会播放一句面试官开场白：{TTS_SAMPLE}
        </p>

        {ttsState === 'idle' && (
          <div className="dc-box dc-box--info">
            <IconVolume size={16} />
            <span>
              请先把系统音量调到能听清的位置。后端上报的语音提供方：
              {ttsOk ? TTS_LABEL[effTts] || TTS_LABEL.browser : '不可用'}
              ；试听之后以实测结果为准。
            </span>
          </div>
        )}

        {(ttsState === 'loading' || ttsState === 'playing') && (
          <div className="dc-box dc-box--wait" role="status">
            <span className="dc-spin" aria-hidden="true" />
            <span>{ttsState === 'loading' ? '正在生成面试官语音…' : '正在播放，请听…'}</span>
          </div>
        )}

        {ttsFallback && (
          <div className="dc-box dc-box--degrade">
            <IconWarn size={16} />
            <span>已降级：当前用的是浏览器朗读，不是云端语音，音色和正式面试可能略有差异。</span>
          </div>
        )}

        {ttsMsg && (
          <div className="dc-box dc-box--info" role="status">
            <IconInfo size={16} />
            <span>{ttsMsg}</span>
          </div>
        )}

        {ttsState === 'asking' && (
          <>
            <div className="dc-box dc-box--ask" role="status">
              <IconVolume size={16} />
              <span>刚才那句面试官开场白，你听得清吗？</span>
            </div>
            <div className="dc-row">
              <button
                type="button"
                className="btn btn--ghost btn--sm"
                onClick={confirmNotHeard}
              >
                <IconVolumeOff size={16} />
                听不到
              </button>
              <button
                type="button"
                className="btn btn--ghost btn--sm"
                onClick={() => setTtsState('skipped')}
              >
                <IconKeyboard size={16} />
                跳过试听
              </button>
            </div>
          </>
        )}

        {ttsState === 'failed' && (
          <div className="dc-row">
            <button
              type="button"
              className="btn btn--ghost btn--sm"
              onClick={() => {
                setTtsState('skipped')
                setTtsUsable(false)
                setPhase('summary')
              }}
            >
              <IconKeyboard size={16} />
              跳过试听，用文字模式
            </button>
          </div>
        )}

        {ttsState === 'skipped' && (
          <div className="dc-box dc-box--info" role="status">
            <IconInfo size={16} />
            <span>
              已跳过试听：结论里仍按后端上报的提供方记录；正式面试如果听不到，
              随时可以切到文字模式继续。
            </span>
          </div>
        )}
      </div>
    )
  }

  /* ============================ 结论页 ============================ */
  const result = buildResult()
  const summaryView = (
    <div className="dc-block">
      <div
        className={`dc-summary-line${result.ok ? ' is-ok' : ' is-warn'}`}
        role="status"
      >
        <span className="dc-summary-ico">
          {result.ok ? <IconCheckCircle size={18} /> : <IconWarn size={18} />}
        </span>
        <span className="dc-summary-text">{result.summary}</span>
      </div>
      <p className="dc-summary-note">
        上面这一行只写本次实际生效的提供方（来自后端 /api/health 与刚才的实测），不会显示未生效的语音服务。
      </p>

      {result.textMode && (
        <div className="dc-textmode">
          <div className="dc-textmode-head">
            <IconKeyboard size={18} />
            <span>
              建议用 <b className="dc-tm-chip">文字模式</b>
            </span>
          </div>
          <p>面试照常进行：提问以文字显示，你打字作答，追问、评分与报告完全一致。</p>
        </div>
      )}

      <ul className="dc-legend">
        <li>
          <b>麦克风</b>
          {result.mic === 'ok'
            ? '已授权且检测到人声'
            : result.mic === 'silent'
              ? '已授权，但没有检测到声音'
              : result.mic === 'denied'
                ? '未授权（面试中将无法语音作答）'
                : '当前环境不支持'}
        </li>
        <li>
          <b>语音识别</b>
          {result.asr === 'aliyun'
            ? '阿里云一句话识别（需要联网）'
            : result.asr === 'browser'
              ? '浏览器自带 Web Speech，无需云端服务'
              : '不可用，请用文字模式作答'}
        </li>
        <li>
          <b>面试官语音</b>
          {result.tts === 'browser'
            ? '浏览器朗读（零成本兜底）'
            : `${TTS_LABEL[result.tts] || ''} 合成后由浏览器播放`}
        </li>
      </ul>
    </div>
  )

  return (
    <div
      className="dc-mask"
      onClick={(e) => {
        if (e.target === e.currentTarget) handleDismiss()
      }}
    >
      <div
        className="dc-sheet"
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        aria-describedby={privacyId}
        aria-busy={primary.busy ? 'true' : undefined}
        tabIndex={-1}
        ref={dialogRef}
      >
        <header className="dc-head">
          <span className="dc-head-ico">
            <IconScanCheck size={22} />
          </span>
          <div className="dc-head-main">
            <h2 className="dc-title" id={titleId}>
              面试前 30 秒设备自检
            </h2>
            <p className="dc-privacy" id={privacyId}>
              <IconLock size={13} />
              全程不录音、不上传，只实时测音量；结束立刻释放麦克风。
            </p>
          </div>
          <button
            type="button"
            className="dc-x"
            data-nofocus
            onClick={handleDismiss}
            aria-label="关闭设备自检"
            title="关闭"
          >
            <IconMinus size={18} />
          </button>
        </header>

        <ol className="dc-steps">
          {STEPS.map((s, i) => {
            const tone = reach(i) ? stepTone(i) : 'todo'
            const now = phase === 'running' && i === step
            return (
              <li
                key={s.key}
                className={`dc-step is-${tone}${now ? ' is-now' : ''}`}
                aria-current={now ? 'step' : undefined}
              >
                <span className="dc-step-dot">
                  {tone === 'ok' ? (
                    <IconCheck size={13} />
                  ) : tone === 'warn' ? (
                    <IconWarn size={13} />
                  ) : (
                    i + 1
                  )}
                </span>
                <span className="dc-step-label">{s.label}</span>
              </li>
            )
          })}
        </ol>

        <div className="dc-body">
          {phase === 'running' ? (
            <>
              <div className="dc-step-head">
                <h3 className="dc-step-title">
                  第 {step + 1} / {STEPS.length} 步 · {STEP_HEAD[step].title}
                </h3>
                <p className="dc-step-desc">{STEP_HEAD[step].desc}</p>
              </div>
              {body}
            </>
          ) : (
            summaryView
          )}
        </div>

        <footer className="dc-foot">
          {phase === 'running' ? (
            <button type="button" className="link-btn dc-skip" onClick={handleSkipAll}>
              {mic === 'ok' ? '跳过自检' : '跳过自检，直接用文字模式'}
            </button>
          ) : (
            <span className="dc-foot-note">
              <IconBulb size={14} />
              随时可以在面试页切换语音/文字模式
            </span>
          )}

          <div className="dc-foot-right">
            {phase === 'running' && step > 0 && (
              <button
                type="button"
                className="btn btn--ghost btn--sm"
                onClick={() => setStep(step - 1)}
              >
                上一步
              </button>
            )}
            {phase === 'running' ? (
              <button
                type="button"
                className="btn btn--primary btn--sm"
                data-autofocus
                disabled={!!primary.busy}
                onClick={primary.onClick}
              >
                {primary.busy && <span className="dc-spin" aria-hidden="true" />}
                {primary.icon}
                {primary.label}
              </button>
            ) : (
              <button
                type="button"
                className="btn btn--primary"
                data-autofocus
                onClick={() => onClose?.()}
              >
                <IconCheck size={16} />
                进入面试
              </button>
            )}
          </div>
        </footer>
      </div>
    </div>
  )
}

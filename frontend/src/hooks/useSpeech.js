import { useCallback, useEffect, useRef, useState } from 'react'
import { ttsSynthesize } from '../api/client'

/** 男女声读速（百分比字符串，正值加速、负值减速），可按需分别调整 */
const RATE_BY_GENDER = {
  male: '+15%',
  female: '+10%',
}

/** 把 "+10%" 这类百分比换算成 speechSynthesis 需要的倍率（1 = 原速） */
const rateNumber = (gender) => {
  const pct = parseInt(String(RATE_BY_GENDER[gender] || '+0%').replace(/[^0-9-]/g, ''), 10) || 0
  return Math.max(0.5, Math.min(2, 1 + pct / 100))
}

/** 在浏览器自带音色里挑一个中文音色，尽量贴合面试官性别 */
const pickBrowserVoice = (gender) => {
  const synth = typeof window !== 'undefined' ? window.speechSynthesis : null
  if (!synth?.getVoices) return null
  const zh = (synth.getVoices() || []).filter((v) => /zh|cmn/i.test(v.lang || v.name || ''))
  if (!zh.length) return null
  const hint = gender === 'male' ? /yunxi|yunyang|kangkang|male/i : /xiaoxiao|xiaoyi|huihui|female/i
  return zh.find((v) => hint.test(v.name || '')) || zh[0]
}

/**
 * 「朗读」能力：三级降级链路（对齐文档 §4.7）
 *
 * 1) 后端音频（阿里云 / edge-tts）：POST /api/tts/synthesize → audio_url → <audio> 播放，
 *    并用 AudioContext + AnalyserNode 计算 RMS 得到 volume(0-1) 驱动嘴型，
 *    用返回的 sentences(start/end) 在 requestAnimationFrame 里比对 audio.currentTime 切换字幕句；
 * 2) 后端返回 provider="browser"（未配置云 TTS / SDK 缺失 / 无网络）时，
 *    退回**浏览器原生 speechSynthesis**，字幕按后端估算的时间轴推进 —— 面试官始终有声音；
 * 3) 浏览器也不支持时静音，仅字幕。
 *
 * 之所以保留第 2 级：只用后端音频时，任何后端语音不可用都会变成「面试官完全不出声」，
 * 用户看到的就是一句「语音加载失败」，而这恰恰是最需要听得见的场景。
 * 只有第 1 级没有产出音频时才走第 2 级，因此不存在双声道/重复朗读。
 *
 * 暴露：speak(text, gender) -> Promise<void>（朗读结束 resolve）、stop()、
 *       volume、currentSentenceIndex、provider、error，
 *       以及字幕同步所需的 sentenceTimeline（分句时间轴）与 playbackTime()（播放位置 getter）。
 */

/**
 * 把播放位置（秒）映射到分句下标。
 *
 * 兼容两种后端时间轴：
 * - edge-tts / 阿里云带字级时间戳：start/end 就是真实音频时间
 * - 估算时间轴（无字级时间戳）：按 4.5 字/秒推算，与真实音频总长可能略有出入
 *
 * 因此当音频真实总长（duration）与时间轴末尾差异明显时，按比例缩放，
 * 否则整句字幕会随播放推进而越差越多（长文本尤其明显）。
 */
function sentenceIndexAt(pos, sentenceObjs, duration = 0) {
  if (!sentenceObjs?.length) return -1
  const last = sentenceObjs[sentenceObjs.length - 1]
  const timelineEnd = last?.end ?? 0
  // duration 可信且与时间轴明显不一致时做线性校正；差异小于 8% 视为误差，不必校正
  const scale =
    duration > 0.5 && timelineEnd > 0.5 && Math.abs(duration - timelineEnd) / timelineEnd > 0.08
      ? timelineEnd / duration
      : 1
  const t = pos * scale
  let idx = sentenceObjs.findIndex((s) => t >= (s.start ?? 0) && t < (s.end ?? 0))
  if (idx < 0 && t >= timelineEnd) idx = sentenceObjs.length - 1
  return idx
}

export default function useSpeech() {
  const [speaking, setSpeaking] = useState(false)
  const [volume, setVolume] = useState(0)
  const [sentenceIndex, setSentenceIndex] = useState(0)
  // 完整分句时间轴（含 start/end），供字幕按音频真实时长反推打字速率
  const [sentenceObjects, setSentenceObjects] = useState([])
  const [provider, setProvider] = useState('')
  const [error, setError] = useState('')

  const audioRef = useRef(null)
  const ctxRef = useRef(null)
  const srcRef = useRef(null)
  const analyserRef = useRef(null)
  const rafRef = useRef(0)
  const volRef = useRef(0)
  const lastVolPushRef = useRef(0)
  const resolvedRef = useRef(true)
  const settleRef = useRef(null)
  const watchdogRef = useRef(0)
  const speakingRef = useRef(false)
  // 浏览器语音兜底时，按后端估算的时间轴推进字幕的定时器
  const sentTimersRef = useRef([])
  // 当前这一句浏览器语音：cancel() 会异步触发上一句的 onerror，
  // 用它做代际校验，避免上一句的回调把这一句的 Promise 提前 resolve
  const utterRef = useRef(null)
  // 朗读代际：每次 speak 递增，异步 TTS 响应落地时校验，已被更新的朗读取代则丢弃
  const activeGenRef = useRef(0)
  // 当前这一句音频元素：供 playbackTime() 按需读取播放位置（字幕用它反推打字速率）
  const liveAudioRef = useRef(null)
  // 浏览器语音兜底时没有 audio 元素，用起始时刻 + 语速倍率折算播放位置
  const browserStartRef = useRef({ at: 0, rate: 1 })

  /**
   * 当前朗读进度（秒）。字幕按它反推打字速率，从而与语音同步。
   *
   * 这是**回调而不是 state**：字幕每帧都要读它，若做成 state 会让整个面试页
   * 每秒重渲染几十次。用 getter 则只有字幕条自己按需取用。
   *
   * - 后端音频链路：直接用 audio.currentTime（真实播放位置，最准）
   * - 浏览器语音兜底：没有音频元素，用 (now - 起始时刻) × 语速倍率
   * - 未在朗读：返回 0
   */
  const playbackTime = useCallback(() => {
    const audio = liveAudioRef.current
    if (audio && speakingRef.current) return audio.currentTime || 0
    if (speakingRef.current && browserStartRef.current.at) {
      return ((performance.now() - browserStartRef.current.at) / 1000) * browserStartRef.current.rate
    }
    return 0
  }, [])

  /* ---------------- 音量写入（节流，避免 60fps 重渲染）---------------- */
  const pushVolume = useCallback((v, force = false) => {
    const val = Math.max(0, Math.min(1, v))
    const now = performance.now()
    if (!force && Math.abs(val - volRef.current) < 0.035 && now - lastVolPushRef.current < 70) return
    volRef.current = val
    lastVolPushRef.current = now
    setVolume(val)
  }, [])

  /** 清理后端音频链路资源 */
  const teardownAudio = useCallback(() => {
    cancelAnimationFrame(rafRef.current)
    rafRef.current = 0
    const audio = audioRef.current
    if (audio) {
      try {
        audio.pause()
        audio.src = ''
      } catch {
        /* 忽略 */
      }
      audioRef.current = null
    }
    // 字幕是「当前读到第几秒」的唯一消费者，停止时必须一并失效，
    // 否则下一句会从上一句的残留位置开始，字幕与语音错位。
    liveAudioRef.current = null
    browserStartRef.current = { at: 0, rate: 1 }
    // 浏览器语音兜底链路的资源同样要清掉，否则 stop() 之后还会继续出声
    sentTimersRef.current.forEach(clearTimeout)
    sentTimersRef.current = []
    utterRef.current = null // 先置空：cancel() 触发的回调会被代际校验挡掉
    try {
      window.speechSynthesis?.cancel?.()
    } catch {
      /* 忽略 */
    }
    try {
      srcRef.current?.disconnect?.()
    } catch {
      /* 忽略 */
    }
    try {
      analyserRef.current?.disconnect?.()
    } catch {
      /* 忽略 */
    }
    srcRef.current = null
    analyserRef.current = null
    const ctx = ctxRef.current
    ctxRef.current = null
    if (ctx && ctx.state !== 'closed') {
      try {
        ctx.close()
      } catch {
        /* 忽略 */
      }
    }
    pushVolume(0, true)
  }, [pushVolume])

  /* ---------------- 完成 / 复位 ---------------- */
  const finish = useCallback(() => {
    cancelAnimationFrame(rafRef.current)
    clearTimeout(watchdogRef.current)
    watchdogRef.current = 0
    speakingRef.current = false
    setSpeaking(false)
    pushVolume(0, true)
    if (!resolvedRef.current) {
      resolvedRef.current = true
      const done = settleRef.current
      settleRef.current = null
      done?.()
    }
  }, [pushVolume])

  /**
   * 看门狗：按文本长度估算一个上限时长，到时强制 finish，
   * 避免 Chrome 长文本卡在 speaking=true 但 onended 不触发而永远阻塞流程。
   */
  const armWatchdog = useCallback(
    (text, onTimeout) => {
      clearTimeout(watchdogRef.current)
      const chars = (text || '').length
      const ms = Math.min(180000, Math.max(8000, (chars / 3) * 1000 + 5000))
      watchdogRef.current = setTimeout(() => {
        if (!speakingRef.current) return
        onTimeout?.()
        finish()
      }, ms)
    },
    [finish]
  )

  /** 停止朗读（可随时中断，未完成的 Promise 也会 resolve） */
  const stop = useCallback(() => {
    teardownAudio()
    finish()
  }, [finish, teardownAudio])

  /* ---------------- 后端音频播放 ---------------- */
  const speakByAudio = useCallback(
    (url, sentenceObjs, plainText, srcProvider = 'aliyun') =>
      new Promise((resolve) => {
        const audio = new Audio(url)
        audio.preload = 'auto'
        audioRef.current = audio
        liveAudioRef.current = audio
        resolvedRef.current = false
        settleRef.current = resolve
        speakingRef.current = true
        setSpeaking(true)
        // 用后端返回的真实提供方标记当前音源（aliyun / edge），而非硬编码
        setProvider(srcProvider)
        setSentenceIndex(0)

        let usedAnalyser = false
        try {
          const Ctx = window.AudioContext || window.webkitAudioContext
          if (Ctx) {
            const ctx = new Ctx()
            const src = ctx.createMediaElementSource(audio)
            const analyser = ctx.createAnalyser()
            analyser.fftSize = 1024
            analyser.smoothingTimeConstant = 0.72
            src.connect(analyser)
            analyser.connect(ctx.destination)
            ctxRef.current = ctx
            srcRef.current = src
            analyserRef.current = analyser
            usedAnalyser = true
            ctx.resume?.()
          }
        } catch {
          usedAnalyser = false
        }

        const buf = usedAnalyser ? new Uint8Array(analyserRef.current.fftSize) : null
        const t0 = performance.now()
        const loop = () => {
          if (!speakingRef.current) return
          if (usedAnalyser && analyserRef.current && buf) {
            analyserRef.current.getByteTimeDomainData(buf)
            let sum = 0
            for (let i = 0; i < buf.length; i += 1) {
              const d = (buf[i] - 128) / 128
              sum += d * d
            }
            const rms = Math.sqrt(sum / buf.length)
            // RMS 偏小 → 放大并做快攻慢放，口型更自然
            pushVolume(Math.min(1, rms * 3.4))
          } else {
            const t = (performance.now() - t0) / 1000
            pushVolume(0.3 + Math.abs(Math.sin(t * 7)) * 0.45 + (Math.random() - 0.5) * 0.12)
          }
          // 字幕切句：比对 audio.currentTime 与 sentences 的 start/end
          const ct = audio.currentTime || 0
          const idx = sentenceIndexAt(ct, sentenceObjs, audio.duration || 0)
          if (idx >= 0) setSentenceIndex(idx)
          rafRef.current = requestAnimationFrame(loop)
        }

        audio.onended = () => {
          cancelAnimationFrame(rafRef.current)
          finish()
        }
        audio.onerror = () => {
          if (audioRef.current !== audio) return // 已被 stop() 中断，非真实加载错误
          setError('后端语音加载失败，请检查网络或阿里云配置')
          finish()
        }

        armWatchdog(plainText, () => setError('后端语音播放超时，已自动进入下一题（字幕正常显示）'))

        audio
          .play()
          .then(() => {
            rafRef.current = requestAnimationFrame(loop)
          })
          .catch(() => {
            if (audioRef.current !== audio) return // 正常被 stop() 中断
            setError('浏览器阻止了自动播放，已切换为纯文字模式')
            finish()
          })
      }),
    [armWatchdog, finish, pushVolume, teardownAudio]
  )

  /* ---------------- 浏览器语音兜底（后端没有音频产出时） ---------------- */
  const speakByBrowser = useCallback(
    (plainText, sentenceObjs, gender = 'female') =>
      new Promise((resolve) => {
        const synth = typeof window !== 'undefined' ? window.speechSynthesis : null
        const Utter = typeof window !== 'undefined' ? window.SpeechSynthesisUtterance : null
        if (!synth || !Utter) {
          setError('浏览器不支持语音合成，已静音（字幕可正常查看）')
          setSpeaking(false)
          resolve()
          return
        }

        resolvedRef.current = false
        settleRef.current = resolve
        speakingRef.current = true
        setSpeaking(true)
        setProvider('browser')
        setSentenceIndex(0)

        const rate = rateNumber(gender)
        const utter = new Utter(plainText)
        utter.lang = 'zh-CN'
        const voice = pickBrowserVoice(gender)
        if (voice) utter.voice = voice
        utter.rate = rate
        utter.pitch = 1

        // 字幕：后端给的是按字数估算的时间轴，按语速倍率折算后逐句推进
        liveAudioRef.current = null // 浏览器语音链路没有 audio 元素
        browserStartRef.current = { at: performance.now(), rate }
        sentTimersRef.current.forEach(clearTimeout)
        sentTimersRef.current = (sentenceObjs || []).map((s, i) =>
          setTimeout(() => setSentenceIndex(i), Math.max(0, ((s.start ?? 0) / rate) * 1000))
        )

        utter.onend = () => {
          if (utterRef.current !== utter) return // 已被 stop()/新朗读取代
          finish()
        }
        utter.onerror = (e) => {
          if (utterRef.current !== utter) return // 上一句被打断的回调，忽略
          const reason = e?.error || ''
          // interrupted / canceled 是 stop() 主动打断，不是故障
          if (reason !== 'interrupted' && reason !== 'canceled') {
            setError(`浏览器语音播放失败（${reason || '未知原因'}），已静音，字幕正常显示`)
          }
          finish()
        }

        armWatchdog(plainText, () =>
          setError('浏览器语音播放超时，已自动进入下一题（字幕正常显示）')
        )

        try {
          synth.cancel() // 清掉可能排队中的上一句，避免两句叠读
          utterRef.current = utter
          synth.speak(utter)
        } catch (e) {
          // speak() 抛异常时绝不能把 Promise 挂着：面试流程会卡在「面试官正在提问…」
          setError(`浏览器语音不可用（${e?.message || '未知原因'}），已静音，字幕正常显示`)
          finish()
        }
      }),
    [armWatchdog, finish]
  )

  /* ---------------- 对外：speak ---------------- */
  const speak = useCallback(
    async (text, gender = 'female') => {
      const plain = (text || '').trim()
      const gen = ++activeGenRef.current
      stop()
      setError('')
      if (!plain) return

      let data = null
      try {
        data = await ttsSynthesize(plain, gender, RATE_BY_GENDER[gender] || '+10%')
      } catch {
        setError('语音服务不可用')
      }

      // 等待 TTS 期间若又发起更晚的朗读（代际已变），丢弃这次结果
      if (gen !== activeGenRef.current) return

      const useBackend =
        data && data.provider !== 'browser' && data.audio_url && data.audio_url.length > 0

      if (useBackend) {
        const sentenceObjs = Array.isArray(data.sentences) && data.sentences.length
          ? data.sentences
          : [{ text: data.fallback_text || plain, start: 0, end: 0 }]
        setSentenceObjects(sentenceObjs)
        return speakByAudio(data.audio_url, sentenceObjs, data.fallback_text || plain, data.provider)
      }

      // 后端没有产出音频（未配置云 TTS / SDK 缺失 / 无网络）→ 用浏览器语音兜底，
      // 保证面试官始终会出声。旧实现在这里直接静音，用户只能看到「语音加载失败」。
      const browserObjs = Array.isArray(data?.sentences) && data.sentences.length
        ? data.sentences
        : [{ text: plain, start: 0, end: 0 }]
      setSentenceObjects(browserObjs)
      setError('后端语音服务不可用，已改用浏览器语音朗读（音质略有差异，字幕正常）')
      return speakByBrowser(plain, browserObjs, gender)
    },
    [speakByAudio, speakByBrowser, stop]
  )

  useEffect(() => () => stop(), [stop])

  return {
    speak,
    stop,
    speaking,
    volume,
    currentSentenceIndex: sentenceIndex,
    /**
     * 完整分句时间轴 [{ text, start, end }]。
     * SubtitleBar 用它按音频真实时长反推打字速率，实现字幕与语音同步。
     */
    sentenceTimeline: sentenceObjects,
    /** 当前播放位置（秒）—— 回调，字幕每帧按需读取，不做 state 以防高频重渲染 */
    playbackTime,
    provider,
    error,
    clearError: () => setError(''),
  }
}
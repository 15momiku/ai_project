import { Component, useEffect, useMemo, useRef, useState } from 'react'
import { IconUser } from './icons'
import usePageVisible from '../hooks/useVisibility'

/**
 * 2D 数字人面试官（纯手绘 SVG 半身像）
 *
 * props
 *  - gender   'female' | 'male'      女性：深色长发 + 西装外套 + 衬衫；男性：短发 + 西装 + 领带
 *  - speaking 是否正在说话            控制待机/说话动画与口型
 *  - volume   0-1 音量                 驱动嘴型（闭合/微张/中张/大张/圆唇）
 *  - blink    可选，外部强制眨眼
 *  - muted    静音时闭嘴（待机口型）
 *
 * 分层：背景 → 身体（呼吸）→ 头发后层 → 头部（点头）→ 耳朵/眉眼/鼻/嘴 → 头发前层（刘海）
 */
export default function Avatar({
  gender = 'female',
  speaking = false,
  volume = 0,
  blink = false,
  muted = false,
  className = '',
}) {
  const isFemale = gender !== 'male'
  const accent = isFemale ? '#5e5ce6' : '#0a84ff'
  const accent2 = isFemale ? '#0071e3' : '#0a84ff'

  const [blinking, setBlinking] = useState(false)
  const [gaze, setGaze] = useState({ x: 0, y: 0 })
  const blinkTimer = useRef(0)
  // 眨眼与视线各自持有独立定时器：共用一个 ref 会让两个 effect 互相覆盖，
  // 挂载期一过就谁也没被真正清掉（定时器泄漏，卸载后仍在 setState）。
  const gazeTimer = useRef(0)
  // 页面隐藏时停掉装饰性动画：没有观看者，继续跑只是白耗电；
  // 且后台标签页的定时器会被浏览器降频/堆积，切回前台容易一次性集中触发。
  const visible = usePageVisible()

  /* 每 3-6 秒自然眨眼一次 */
  useEffect(() => {
    if (!visible) return undefined
    let alive = true
    let closeTimer = 0
    const schedule = () => {
      const delay = 3000 + Math.random() * 3000
      blinkTimer.current = setTimeout(() => {
        if (!alive) return
        setBlinking(true)
        closeTimer = setTimeout(() => alive && setBlinking(false), 110)
        schedule()
      }, delay)
    }
    schedule()
    return () => {
      alive = false
      clearTimeout(blinkTimer.current)
      clearTimeout(closeTimer)
    }
  }, [visible])

  /* 偶尔的视线微移（待机时更“活”） */
  useEffect(() => {
    if (!visible) return undefined
    let alive = true
    const tick = () => {
      gazeTimer.current = setTimeout(() => {
        if (!alive) return
        setGaze({ x: (Math.random() - 0.5) * 2.2, y: (Math.random() - 0.5) * 1.2 })
        tick()
      }, 2600 + Math.random() * 2600)
    }
    tick()
    return () => {
      alive = false
      clearTimeout(gazeTimer.current)
    }
  }, [visible])

  const lidClosed = blink || blinking

  /* 音量 → 嘴型（0 闭合 / 1 微张 / 2 中张 / 3 大张 / 4 圆唇） */
  const mouthShape = useMemo(() => {
    const v = muted ? 0 : Math.max(0, Math.min(1, volume || 0))
    if (v < 0.07) return 0
    if (v < 0.24) return 1
    if (v < 0.5) return 2
    if (v < 0.76) return 3
    return 4
  }, [volume, muted])

  const jaw = muted ? 0 : Math.min(3.4, Math.max(0, volume || 0) * 3.6)

  const mouthShapes = [
    // 0 闭合：一条唇线
    <path key="m0" d="M -13,0 Q 0,2.6 13,0" fill="none" stroke="#7f3b4d" strokeWidth="2.2" strokeLinecap="round" />,
    // 1 微张
    <ellipse key="m1" cx="0" cy="0.4" rx="11" ry="3.2" fill="#5b2436" />,
    // 2 中张
    <g key="m2">
      <ellipse cx="0" cy="0.6" rx="10.5" ry="6" fill="#4a1c2c" />
      <ellipse cx="0" cy="-2.4" rx="8.4" ry="2.2" fill="#f8fafc" opacity="0.9" />
    </g>,
    // 3 大张
    <g key="m3">
      <ellipse cx="0" cy="1" rx="10" ry="9" fill="#3f1725" />
      <ellipse cx="0" cy="-4" rx="8.2" ry="2.4" fill="#f8fafc" opacity="0.92" />
      <ellipse cx="0" cy="5.4" rx="5.2" ry="3" fill="#c2415f" opacity="0.75" />
    </g>,
    // 4 圆唇
    <g key="m4">
      <ellipse cx="0" cy="0.8" rx="7" ry="8.4" fill="#4a1c2c" />
      <ellipse cx="0" cy="0.8" rx="4.2" ry="5.2" fill="#3a1420" />
    </g>,
  ]

  return (
    <div className={`avatar-wrap ${className}`}>
      <svg
        className={`avatar-svg ${speaking ? 'av-speaking' : ''} ${visible ? '' : 'av-paused'}`}
        viewBox="0 0 320 380"
        role="img"
        aria-label={isFemale ? '知性女面试官' : '沉稳男面试官'}
        style={{ '--av-accent': accent, '--av-accent-2': accent2 }}
      >
        <defs>
          <linearGradient id="avBg" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="#f5f5f7" />
            <stop offset="60%" stopColor="#eef0f4" />
            <stop offset="100%" stopColor="#e8ebf1" />
          </linearGradient>
          <radialGradient id="avLight" cx="50%" cy="18%" r="62%">
            <stop offset="0%" stopColor={accent} stopOpacity="0.22" />
            <stop offset="100%" stopColor={accent} stopOpacity="0" />
          </radialGradient>
          <linearGradient id="avSuit" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="#d5dae4" />
            <stop offset="100%" stopColor="#b9c0cd" />
          </linearGradient>
          <linearGradient id="avShirt" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="#fbfcfe" />
            <stop offset="100%" stopColor="#e7eaf1" />
          </linearGradient>
          <linearGradient id="avSkin" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="#f6d9c6" />
            <stop offset="100%" stopColor="#e9bfa6" />
          </linearGradient>
          <linearGradient id="avSkinDark" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="#e2b499" />
            <stop offset="100%" stopColor="#cfa088" />
          </linearGradient>
          <linearGradient id="avHair" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor={isFemale ? '#40323a' : '#3a3f4a'} />
            <stop offset="100%" stopColor={isFemale ? '#2b2129' : '#252932'} />
          </linearGradient>
          <linearGradient id="avAccent" x1="0" y1="0" x2="1" y2="1">
            <stop offset="0%" stopColor={accent} />
            <stop offset="100%" stopColor={accent2} />
          </linearGradient>
        </defs>

        {/* ---------- 背景：简约办公室 ---------- */}
        <rect x="0" y="0" width="320" height="380" rx="26" fill="url(#avBg)" />
        <rect x="0" y="0" width="320" height="380" rx="26" fill="url(#avLight)" />
        <g opacity="0.5">
          <rect x="230" y="34" width="62" height="86" rx="8" fill="#e8ebf1" stroke={accent} strokeOpacity="0.3" />
          <line x1="261" y1="34" x2="261" y2="120" stroke={accent} strokeOpacity="0.22" />
          <line x1="230" y1="77" x2="292" y2="77" stroke={accent} strokeOpacity="0.22" />
          <rect x="28" y="52" width="46" height="60" rx="6" fill="#eef0f4" opacity="0.8" />
          <rect x="36" y="140" width="16" height="26" rx="3" fill="#e2e6ee" opacity="0.9" />
          <rect x="18" y="166" width="52" height="9" rx="3" fill="#e2e6ee" opacity="0.9" />
        </g>
        <path d="M0,330 C 70,300 250,300 320,330 L320,354 C320,368 308,380 294,380 L26,380 C12,380 0,368 0,354 Z" fill="#e2e6ee" opacity="0.85" />

        {/* ---------- 身体（呼吸浮动） ---------- */}
        <g className="av-body">
          {/* 西装 */}
          <path
            d="M 22,380 C 26,322 54,292 100,278 L 126,270 L 160,300 L 194,270 L 220,278 C 266,292 294,322 298,380 Z"
            fill="url(#avSuit)"
            stroke="var(--av-accent)"
            strokeOpacity="0.4"
            strokeWidth="1.4"
          />
          {/* 衬衫主体 */}
          <path
            d="M 126,270 C 140,285 150,296 160,300 C 170,296 180,285 194,270 L 200,273 C 187,302 177,342 175,380 L 145,380 C 143,342 133,302 120,273 Z"
            fill="url(#avShirt)"
            opacity="0.95"
          />
          {/* 领子 */}
          <path d="M 124,268 L 160,300 L 136,308 Z" fill="#f2f5fb" />
          <path d="M 196,268 L 160,300 L 184,308 Z" fill="#e6ebf6" />
          {/* 翻领：比西装稍深一档，才能在白色页面里读出衣领层次 */}
          <path d="M 100,278 L 126,270 L 149,318 L 122,342 Z" fill="#c7cedb" stroke="var(--av-accent)" strokeOpacity="0.3" />
          <path d="M 220,278 L 194,270 L 171,318 L 198,342 Z" fill="#c7cedb" stroke="var(--av-accent)" strokeOpacity="0.3" />
          {isFemale ? (
            /* 女性：细项链 + 内搭质感（无领带） */
            <>
              <path d="M 138,308 Q 160,332 182,308" fill="none" stroke="url(#avAccent)" strokeWidth="2.4" strokeLinecap="round" />
              <circle cx="160" cy="326" r="3.2" fill="url(#avAccent)" />
            </>
          ) : (
            /* 男性：领带 */
            <>
              <path d="M 160,300 L 172,318 L 165,372 L 155,372 L 148,318 Z" fill="url(#avAccent)" opacity="0.92" />
              {/* 领带结高光：白底主题下用白色微透，避免整条领带糊成一片 */}
              <path d="M 148,318 L 172,318 L 170,324 L 150,324 Z" fill="#ffffff" opacity="0.38" />
            </>
          )}
        </g>

        {/* ---------- 头发后层（女性长发） ---------- */}
        {isFemale && (
          <path
            d="M 96,206 C 92,150 122,124 160,124 C 198,124 228,150 224,206 C 228,252 236,300 228,336 L 204,336 C 210,300 210,272 210,252 C 208,286 206,312 202,338 L 118,338 C 114,312 112,286 110,252 C 110,272 110,300 116,336 L 92,336 C 84,300 92,252 96,206 Z"
            fill="url(#avHair)"
          />
        )}

        {/* ---------- 头部（点头动画） ---------- */}
        <g className="av-head">
          {/* 脖子 + 下颌阴影 */}
          <path d="M 144,240 L 176,240 L 178,282 L 142,282 Z" fill="url(#avSkinDark)" />
          <path d="M 142,258 C 152,272 168,272 178,258 L 178,282 L 142,282 Z" fill="#c08e77" opacity="0.55" />
          {/* 耳朵 */}
          <ellipse cx="108" cy="212" rx="7.5" ry="11.5" fill="url(#avSkinDark)" />
          <ellipse cx="212" cy="212" rx="7.5" ry="11.5" fill="url(#avSkinDark)" />
          {/* 脸 */}
          <path
            d="M 110,200 C 110,166 130,147 160,147 C 190,147 210,166 210,200 C 210,240 190,272 160,277 C 130,272 110,240 110,200 Z"
            fill="url(#avSkin)"
          />
          {/* 腮红 */}
          <ellipse cx="128" cy="228" rx="10" ry="6" fill="#e88a8a" opacity="0.16" />
          <ellipse cx="192" cy="228" rx="10" ry="6" fill="#e88a8a" opacity="0.16" />

          {/* 眉毛 */}
          <g className="av-brow">
            <path d="M 127,184 Q 139,177 151,183" fill="none" stroke="#3b3346" strokeWidth="3.6" strokeLinecap="round" />
            <path d="M 193,184 Q 181,177 169,183" fill="none" stroke="#3b3346" strokeWidth="3.6" strokeLinecap="round" />
          </g>

          {/* 眼睛 */}
          <g>
            <ellipse cx="136" cy="203" rx="13" ry="9.2" fill="#f8fafc" />
            <ellipse cx="184" cy="203" rx="13" ry="9.2" fill="#f8fafc" />
            <g transform={`translate(${gaze.x},${gaze.y})`}>
              <circle cx="136" cy="203" r="6.6" fill="#3b2a4a" />
              <circle cx="184" cy="203" r="6.6" fill="#3b2a4a" />
              <circle cx="136" cy="203" r="3.1" fill="#141024" />
              <circle cx="184" cy="203" r="3.1" fill="#141024" />
              <circle cx="138.4" cy="200.4" r="1.9" fill="#fff" opacity="0.92" />
              <circle cx="186.4" cy="200.4" r="1.9" fill="#fff" opacity="0.92" />
            </g>
            {/* 眼皮：眨眼时从上方合下 */}
            <ellipse className={`av-eyelid ${lidClosed ? 'closed' : ''}`} cx="136" cy="203" rx="13" ry="9.2" fill="url(#avSkin)" />
            <ellipse className={`av-eyelid ${lidClosed ? 'closed' : ''}`} cx="184" cy="203" rx="13" ry="9.2" fill="url(#avSkin)" />
            {/* 眼线 */}
            <path d="M 123,199 Q 136,192 149,199" fill="none" stroke="#2f2739" strokeWidth="2.2" strokeLinecap="round" />
            <path d="M 197,199 Q 184,192 171,199" fill="none" stroke="#2f2739" strokeWidth="2.2" strokeLinecap="round" />
          </g>

          {/* 鼻子 */}
          <path d="M 158,212 Q 153,226 161,229 Q 165.5,230 169,226.5" fill="none" stroke="#cf9d86" strokeWidth="2.2" strokeLinecap="round" />

          {/* 嘴：5 种口型交叉淡入 + 下颌位移，100ms 平滑过渡 */}
          <g className="av-mouth" transform={`translate(160,${244 + jaw})`}>
            {mouthShapes.map((shape, i) => (
              <g key={i} className="av-mouth-shape" style={{ opacity: mouthShape === i ? 1 : 0 }}>
                {shape}
              </g>
            ))}
          </g>
        </g>

        {/* ---------- 头发前层（刘海 / 短发） ---------- */}
        {isFemale ? (
          <path
            d="M 108,198 C 110,158 132,140 160,140 C 188,140 210,158 212,198 C 202,176 188,166 160,166 C 132,166 118,176 108,198 Z"
            fill="url(#avHair)"
          />
        ) : (
          <path
            d="M 110,200 C 110,158 132,140 160,140 C 188,140 210,158 210,200 C 204,182 196,172 186,169 C 172,164 148,164 134,169 C 124,172 116,182 110,200 Z"
            fill="url(#avHair)"
          />
        )}

        {/* ---------- 说话时的声波光边 ---------- */}
        {speaking && (
          <g className="av-glow" opacity="0.7">
            <path
              d="M 22,380 C 26,322 54,292 100,278 L 126,270 L 160,300 L 194,270 L 220,278 C 266,292 294,322 298,380"
              fill="none"
              stroke="url(#avAccent)"
              strokeWidth="2"
              strokeOpacity="0.6"
            />
          </g>
        )}
      </svg>
    </div>
  )
}

/**
 * 数字人降级形态：头像 + 音频波形（文档 §4.7）
 * 数字人渲染异常时使用，保证面试流程不中断。
 */
function AvatarFallback({ gender = 'female', speaking = false, volume = 0 }) {
  const bars = 22
  const vol = Math.max(0.04, Math.min(1, volume || 0))
  return (
    <div className="avatar-wrap">
      <div
        className="card card--flat"
        style={{ width: '100%', maxWidth: 340, padding: '18px 14px', textAlign: 'center' }}
      >
        <div
          style={{
            width: 76,
            height: 76,
            borderRadius: '50%',
            margin: '0 auto 12px',
            display: 'grid',
            placeItems: 'center',
            background: gender === 'male' ? 'rgba(10,132,255,.12)' : 'rgba(94,92,230,.12)',
            border: '1px solid var(--line)',
            fontSize: 30,
          }}
        >
          <IconUser size={40} style={{ color: 'var(--text-mute)' }} />
        </div>
        <div className="wave" aria-hidden="true">
          {Array.from({ length: bars }).map((_, i) => {
            const center = Math.abs(i - (bars - 1) / 2) / ((bars - 1) / 2)
            const h = speaking ? 8 + vol * 62 * (1 - center * 0.72) * (0.6 + Math.random() * 0.6) : 6
            return <i key={i} style={{ height: `${Math.max(5, h)}px` }} />
          })}
        </div>
        <div className="mute-2 mt-8" style={{ fontSize: 12 }}>
          {speaking ? '面试官正在说话…' : '数字人动画不可用，已切换为语音波形模式'}
        </div>
      </div>
    </div>
  )
}

/** 错误边界：数字人渲染异常时降级为波形，不影响面试流程 */
export class AvatarBoundary extends Component {
  constructor(props) {
    super(props)
    this.state = { failed: false }
  }

  static getDerivedStateFromError() {
    return { failed: true }
  }

  componentDidCatch(error) {
    console.warn('[Avatar] 渲染异常，已降级为波形模式：', error)
  }

  render() {
    if (this.state.failed) {
      return <AvatarFallback gender={this.props.gender} speaking={this.props.speaking} volume={this.props.volume} />
    }
    return this.props.children
  }
}

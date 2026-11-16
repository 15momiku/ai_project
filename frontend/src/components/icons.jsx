/**
 * 共享矢量图标字汇（手写 SVG，不引入图标库）
 *
 * 设计约定（对应 ui-ux-pro-max 设计库的图标规则）：
 *  - 结构图标一律用矢量，禁止 emoji（emoji 在不同系统渲染不一、粗细不齐、不可控色）
 *  - 24×24 viewBox，统一线宽 1.8，圆头圆角，currentColor 着色
 *  - 实心图标（solid）用于「色块里的图标」与状态勾选：小尺寸下比线框更醒目
 *  - 尺寸走 global.css 的 --icon-* 令牌，默认 20px
 */

function Svg({ children, solid = false, className = '', size = 20, style }) {
  return (
    <svg
      className={className}
      style={style}
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill={solid ? 'currentColor' : 'none'}
      stroke={solid ? 'none' : 'currentColor'}
      strokeWidth={solid ? undefined : 1.8}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
    >
      {children}
    </svg>
  )
}

/* ============================ 导航 ============================ */

export const IconHome = (p) => (
  <Svg {...p}>
    <path d="M3 10.5 12 3l9 7.5" />
    <path d="M5 9.6V20h5.2v-6h3.6v6H19V9.6" />
  </Svg>
)

export const IconMic = (p) => (
  <Svg {...p}>
    <rect x="9" y="3" width="6" height="11" rx="3" />
    <path d="M5 11a7 7 0 0 0 14 0" />
    <path d="M12 18v3" />
  </Svg>
)

export const IconBook = (p) => (
  <Svg {...p}>
    <path d="M4 5.5A2.5 2.5 0 0 1 6.5 3H20v15H6.5A2.5 2.5 0 0 0 4 20.5z" />
    <path d="M4 18.5V21h16" />
    <path d="M9 8h7M9 11.5h5" />
  </Svg>
)

export const IconTrophy = (p) => (
  <Svg {...p}>
    <path d="M7 4h10v5a5 5 0 0 1-10 0z" />
    <path d="M17 5h2.5a2.5 2.5 0 0 1-2.6 4.6M7 5H4.5A2.5 2.5 0 0 0 7.1 9.6" />
    <path d="M12 14v3M8.5 20h7" />
  </Svg>
)

export const IconUser = (p) => (
  <Svg {...p}>
    <circle cx="12" cy="8.5" r="3.8" />
    <path d="M4.8 20a7.2 7.2 0 0 1 14.4 0" />
  </Svg>
)

/* ============================ 模块（首页三大模块）============================ */
/* 实心：用于浅色圆角方块内，小尺寸下辨识度高于线框 */

export const IconTarget = (p) => (
  <Svg {...p} solid>
    <path d="M12 1.8A10.2 10.2 0 1 0 22.2 12 10.21 10.21 0 0 0 12 1.8Zm0 4A6.2 6.2 0 1 1 5.8 12 6.21 6.21 0 0 1 12 5.8Zm0 4A2.2 2.2 0 1 0 14.2 12 2.2 2.2 0 0 0 12 9.8Z" />
  </Svg>
)

export const IconBriefcaseUser = (p) => (
  <Svg {...p} solid>
    <path d="M2.6 7.4A2.4 2.4 0 0 1 5 5h14a2.4 2.4 0 0 1 2.4 2.4v9.2A2.4 2.4 0 0 1 19 19H5a2.4 2.4 0 0 1-2.4-2.4Zm7.9-3.9h3a1 1 0 0 1 1 1v1.3h-2V4.5h-1v1.3h-2V4.5a1 1 0 0 1 1-1Z" />
    <circle cx="12" cy="11" r="1.9" fill="#fff" />
    <path d="M8.6 16.8a3.4 3.4 0 0 1 6.8 0Z" fill="#fff" />
  </Svg>
)

export const IconChart = (p) => (
  <Svg {...p} solid>
    <rect x="3" y="12.5" width="4.2" height="8.5" rx="1.3" />
    <rect x="9.9" y="7" width="4.2" height="14" rx="1.3" />
    <rect x="16.8" y="3" width="4.2" height="18" rx="1.3" />
  </Svg>
)

/* ============================ 语义状态 ============================ */

export const IconCheck = (p) => (
  <Svg {...p}>
    <path d="m4.5 12.6 4.7 4.7L19.5 6.9" />
  </Svg>
)

export const IconCheckCircle = (p) => (
  <Svg {...p} solid>
    <path d="M12 1.8A10.2 10.2 0 1 0 22.2 12 10.21 10.21 0 0 0 12 1.8Zm5.36 7.86-6.1 6.28a1.2 1.2 0 0 1-1.72 0l-2.9-2.99a1.2 1.2 0 1 1 1.72-1.67l2.04 2.1 5.24-5.39a1.2 1.2 0 1 1 1.72 1.67Z" />
  </Svg>
)

export const IconWarn = (p) => (
  <Svg {...p} solid>
    <path d="M10.03 3.63 2.24 17.3A2.27 2.27 0 0 0 4.21 20.7h15.58a2.27 2.27 0 0 0 1.97-3.4L13.97 3.63a2.27 2.27 0 0 0-3.94 0ZM10.9 9.4a1.1 1.1 0 0 1 2.2 0v4a1.1 1.1 0 0 1-2.2 0Zm1.1 7.1a1.35 1.35 0 1 1 1.35-1.35A1.35 1.35 0 0 1 12 16.5Z" />
  </Svg>
)

export const IconBulb = (p) => (
  <Svg {...p}>
    <path d="M9.2 17.2c-.3-1.3-1-2.2-1.8-3.1a5.9 5.9 0 1 1 9.2 0c-.8.9-1.5 1.8-1.8 3.1Z" />
    <path d="M9.5 20.4h5M10.4 17.2h3.2" />
  </Svg>
)

export const IconTrendUp = (p) => (
  <Svg {...p}>
    <path d="M3 17.5 9 11.5l4 4L21 6.5" />
    <path d="M15.5 6.5H21v5.5" />
  </Svg>
)

export const IconInfo = (p) => (
  <Svg {...p}>
    <circle cx="12" cy="12" r="9.2" />
    <path d="M12 11v5.4" />
    <path d="M12 7.9h.01" strokeWidth="2.2" />
  </Svg>
)

/* ============================ 功能 ============================ */

/** 漏斗：转化漏斗板块（顶宽底窄的经典漏斗轮廓，单条闭合路径即可） */
export const IconFunnel = (p) => (
  <Svg {...p}>
    <path d="M3.6 4.8h16.8l-6.3 7.7v6.2l-4.2 2.1v-8.3z" />
  </Svg>
)

/** 多用户：运营明细 / 用户数相关板块（两个人像，后者省略部分线条避免糊成一团） */
export const IconUsers = (p) => (
  <Svg {...p}>
    <circle cx="9.2" cy="8.2" r="3.3" />
    <path d="M3.2 19.8a6 6 0 0 1 12 0" />
    <path d="M16.2 5.4a3.3 3.3 0 0 1 0 5.7" />
    <path d="M17.4 14.6a6 6 0 0 1 3.4 5.2" />
  </Svg>
)

/** 收件箱：列表空态与统计卡片 */
export const IconInbox = (p) => (
  <Svg {...p}>
    <path d="M3.4 13.2 6 5.4A2 2 0 0 1 7.9 4h8.2a2 2 0 0 1 1.9 1.4l2.6 7.8v3.4A2.4 2.4 0 0 1 18.2 19H5.8a2.4 2.4 0 0 1-2.4-2.4z" />
    <path d="M3.4 13.2h4.4l1 2h6.4l1-2h4.4" />
  </Svg>
)

export const IconSparkle = (p) => (
  <Svg {...p} solid>
    <path d="M11.4 2.6a.6.6 0 0 1 1.2 0l1.42 5.18a3 3 0 0 0 2.2 2.2l5.18 1.42a.6.6 0 0 1 0 1.2l-5.18 1.42a3 3 0 0 0-2.2 2.2L12.6 21.4a.6.6 0 0 1-1.2 0l-1.42-5.18a3 3 0 0 0-2.2-2.2L2.6 12.6a.6.6 0 0 1 0-1.2l5.18-1.42a3 3 0 0 0 2.2-2.2Z" />
  </Svg>
)

export const IconCompass = (p) => (
  <Svg {...p} solid>
    <path d="M12 1.8A10.2 10.2 0 1 0 22.2 12 10.21 10.21 0 0 0 12 1.8Zm3.9 6.3-2.23 5.57a1.3 1.3 0 0 1-.72.72L7.38 16.6a.5.5 0 0 1-.65-.65l2.22-5.57a1.3 1.3 0 0 1 .72-.72l5.57-2.23a.5.5 0 0 1 .66.66Z" />
    <circle cx="12" cy="12" r="1.5" fill="#fff" />
  </Svg>
)

export const IconMinus = (p) => (
  <Svg {...p}>
    <circle cx="12" cy="12" r="9.2" />
    <path d="M8 12h8" />
  </Svg>
)

export const IconPlus = (p) => (
  <Svg {...p}>
    <circle cx="12" cy="12" r="9.2" />
    <path d="M12 8v8M8 12h8" />
  </Svg>
)

export const IconUpload = (p) => (
  <Svg {...p}>
    <path d="M12 16V4.5" />
    <path d="M7.5 9 12 4.5 16.5 9" />
    <path d="M4 15.5v2.3A2.2 2.2 0 0 0 6.2 20h11.6a2.2 2.2 0 0 0 2.2-2.2v-2.3" />
  </Svg>
)

export const IconDoc = (p) => (
  <Svg {...p}>
    <path d="M13.5 3H7.4A2.4 2.4 0 0 0 5 5.4v13.2A2.4 2.4 0 0 0 7.4 21h9.2a2.4 2.4 0 0 0 2.4-2.4V8.5Z" />
    <path d="M13.5 3v5.5H19" />
    <path d="M9 13h6M9 16.5h4" />
  </Svg>
)

export const IconClock = (p) => (
  <Svg {...p}>
    <circle cx="12" cy="12" r="9.2" />
    <path d="M12 7.2V12l3.4 2" />
  </Svg>
)

export const IconPlay = (p) => (
  <Svg {...p} solid>
    <path d="M6.4 3.9a1 1 0 0 1 1.52-.85l10.6 6.85a1 1 0 0 1 0 1.7L7.92 20.45A1 1 0 0 1 6.4 19.6Z" />
  </Svg>
)

export const IconRefresh = (p) => (
  <Svg {...p}>
    <path d="M20 12a8 8 0 1 1-2.7-6" />
    <path d="M20 4.5V10h-5.5" />
  </Svg>
)

export const IconTrash = (p) => (
  <Svg {...p}>
    <path d="M4.5 7h15" />
    <path d="M9.5 7V5.2A1.2 1.2 0 0 1 10.7 4h2.6a1.2 1.2 0 0 1 1.2 1.2V7" />
    <path d="M6.4 7 7.2 19a1.6 1.6 0 0 0 1.6 1.5h6.4A1.6 1.6 0 0 0 16.8 19L17.6 7" />
    <path d="M10.5 11v6M13.5 11v6" />
  </Svg>
)

export const IconShare = (p) => (
  <Svg {...p}>
    <path d="M12 15.5V3.8" />
    <path d="M7.8 8 12 3.8 16.2 8" />
    <path d="M5 13.5v4.1A2.4 2.4 0 0 0 7.4 20h9.2a2.4 2.4 0 0 0 2.4-2.4v-4.1" />
  </Svg>
)

export const IconDownload = (p) => (
  <Svg {...p}>
    <path d="M12 3.8v11.7" />
    <path d="M7.8 11.3 12 15.5l4.2-4.2" />
    <path d="M5 18.2v.4A2.4 2.4 0 0 0 7.4 21h9.2a2.4 2.4 0 0 0 2.4-2.4v-.4" />
  </Svg>
)

export const IconLock = (p) => (
  <Svg {...p}>
    <rect x="4.8" y="10.2" width="14.4" height="10.3" rx="2.4" />
    <path d="M8.4 10.2V7.6a3.6 3.6 0 0 1 7.2 0v2.6" />
  </Svg>
)

export const IconVolume = (p) => (
  <Svg {...p}>
    <path d="M4 9.4h3.3L12 5.6v12.8L7.3 14.6H4A1 1 0 0 1 3 13.6v-3.2a1 1 0 0 1 1-1Z" />
    <path d="M15.6 8.9a4.4 4.4 0 0 1 0 6.2" />
    <path d="M18.2 6.4a8 8 0 0 1 0 11.2" />
  </Svg>
)

export const IconVolumeOff = (p) => (
  <Svg {...p}>
    <path d="M4 9.4h3.3L12 5.6v12.8L7.3 14.6H4A1 1 0 0 1 3 13.6v-3.2a1 1 0 0 1 1-1Z" />
    <path d="m16.2 9.5 4.6 5" />
    <path d="m20.8 9.5-4.6 5" />
  </Svg>
)

export const IconKeyboard = (p) => (
  <Svg {...p}>
    <rect x="2.6" y="6.4" width="18.8" height="11.2" rx="2.2" />
    <path d="M6.5 9.9h.01M9.7 9.9h.01M12.9 9.9h.01M16.1 9.9h.01M6.5 13.1h.01M17.4 13.1h.01" strokeWidth="2.2" />
    <path d="M9.6 13.1h5" />
  </Svg>
)

export const IconReplay = (p) => (
  <Svg {...p}>
    <path d="M4 5.5V12h6.5" />
    <path d="M4.6 12a8 8 0 1 1 2.3 6" />
  </Svg>
)

/* ============================ 简历优化 ============================ */

/** 「AI 文档」：文档轮廓 + 右下角的 AI 星标（星标复用 IconSparkle 的成熟路径，等比缩放后平移）
 *  做法：文档右下角刻意留出缺口，星标落进缺口里，不需要背景色遮挡。 */
export const IconFilePen = (p) => (
  <Svg {...p}>
    {/* 文档外框：顶边 + 左侧 + 底边，右下留缺口 */}
    <path d="M13.5 3H7.4A2.4 2.4 0 0 0 5 5.4v13.2A2.4 2.4 0 0 0 7.4 21H12" />
    {/* 折角 + 右边（在缺口前收住） */}
    <path d="M13.5 3v5.5H19V12" />
    {/* 正文行 */}
    <path d="M9 12.4h4.6M9 16.2h3" />
    <path
      fill="currentColor"
      stroke="none"
      transform="translate(12.84 13.04) scale(0.38)"
      d="M11.4 2.6a.6.6 0 0 1 1.2 0l1.42 5.18a3 3 0 0 0 2.2 2.2l5.18 1.42a.6.6 0 0 1 0 1.2l-5.18 1.42a3 3 0 0 0-2.2 2.2L12.6 21.4a.6.6 0 0 1-1.2 0l-1.42-5.18a3 3 0 0 0-2.2-2.2L2.6 12.6a.6.6 0 0 1 0-1.2l5.18-1.42a3 3 0 0 0 2.2-2.2Z"
    />
  </Svg>
)

/** 魔棒 + 星芒：AI 润色。星芒用 8 点折线（外点走正方向、内点走对角），
 *  不依赖曲线，任何尺寸下都不会糊成一团。 */
export const IconWand = (p) => (
  <Svg {...p}>
    <path d="M4.4 19.6 11.6 12.4" />
    <path d="M15.6 4.8 16.8 7.2 19.2 8.4 16.8 9.6 15.6 12 14.4 9.6 12 8.4 14.4 7.2Z" />
    <path d="M7.2 4 8 5.6 9.6 6.4 8 7.2 7.2 8.8 6.4 7.2 4.8 6.4 6.4 5.6Z" />
  </Svg>
)

/** 扫描取景框 + 勾：ATS 兼容性检测 */
export const IconScanCheck = (p) => (
  <Svg {...p}>
    <path d="M4 8.6V6.4A2.4 2.4 0 0 1 6.4 4h2.2" />
    <path d="M15.4 4h2.2A2.4 2.4 0 0 1 20 6.4v2.2" />
    <path d="M20 15.4v2.2a2.4 2.4 0 0 1-2.4 2.4h-2.2" />
    <path d="M8.6 20H6.4A2.4 2.4 0 0 1 4 17.6v-2.2" />
    <path d="m8.9 12.1 2.1 2.1 4.1-4.3" />
  </Svg>
)

/** 证件照 / 图片占位 */
export const IconPhoto = (p) => (
  <Svg {...p}>
    <rect x="3.4" y="4.8" width="17.2" height="14.4" rx="2.4" />
    <circle cx="9" cy="10" r="1.7" />
    <path d="m4.8 17.4 4.4-4.2 3.1 2.9 2.7-2.5 4.2 4" />
  </Svg>
)

/* ============================ 岗位信息汇总 ============================ */
/* 与全站图标同一套字汇：24 viewBox / 1.8 线宽的线框风格，
   线宽与圆角保持一致，避免混排时粗细不齐。 */

/** 公文包：岗位信息汇总（导航图标） */
export const IconBriefcase = (p) => (
  <Svg {...p}>
    <rect x="3" y="7.4" width="18" height="12.2" rx="2.4" />
    <path d="M8.6 7.4V5.6a1.6 1.6 0 0 1 1.6-1.6h3.6a1.6 1.6 0 0 1 1.6 1.6v1.8" />
    <path d="M3 12.4h18" />
    <path d="M10.4 12.4v1.8h3.2v-1.8" />
  </Svg>
)

/** 楼宇：雇主类型分布 */
export const IconBuilding = (p) => (
  <Svg {...p}>
    <path d="M4.4 20.4V5.6A2.2 2.2 0 0 1 6.6 3.4h5.2a2.2 2.2 0 0 1 2.2 2.2v14.8" />
    <path d="M14 9.6h3.4a2.2 2.2 0 0 1 2.2 2.2v8.6" />
    <path d="M2.8 20.4h18.4" />
    <path d="M7.4 7.2h3.2M7.4 10.8h3.2M7.4 14.4h3.2" />
  </Svg>
)

/** 放大镜：列表页搜索框 */
export const IconSearch = (p) => (
  <Svg {...p}>
    <circle cx="10.8" cy="10.8" r="6.4" />
    <path d="m15.6 15.6 4.6 4.6" />
  </Svg>
)

/** 向下箭头：筛选 / 进度选择器的展开指示（比用文字「▾」可控） */
export const IconChevron = (p) => (
  <Svg {...p}>
    <path d="m6.6 9.6 5.4 5.4 5.4-5.4" />
  </Svg>
)

/** 关闭：清掉单个已选筛选条件 */
export const IconX = (p) => (
  <Svg {...p}>
    <path d="m6.6 6.6 10.8 10.8M17.4 6.6 6.6 17.4" />
  </Svg>
)

/** 漏斗线框：筛选栏标题（统计页的漏斗板块用的是实心 IconFunnel） */
export const IconFilter = (p) => (
  <Svg {...p}>
    <path d="M3.6 5.4h16.8l-6.3 7.4v5.6l-4.2 2.2v-7.8z" />
  </Svg>
)

/** 列表：汇总列表标签页 */
export const IconList = (p) => (
  <Svg {...p}>
    <path d="M8.4 6.4h11.2M8.4 12h11.2M8.4 17.6h11.2" />
    <path d="M4.4 6.4h.01M4.4 12h.01M4.4 17.6h.01" strokeWidth="2.2" />
  </Svg>
)

/** 外链：公告链接（新窗口打开，图标本身提示「会离开本页」） */
export const IconExternal = (p) => (
  <Svg {...p}>
    <path d="M13.8 4.6H19.4v5.6" />
    <path d="M19.4 4.6 11.6 12.4" />
    <path d="M17.4 14.2v4.2A2.2 2.2 0 0 1 15.2 20.6H5.8A2.2 2.2 0 0 1 3.6 18.4V9a2.2 2.2 0 0 1 2.2-2.2h4.2" />
  </Svg>
)

/** 箭头向右：投递链接 */
export const IconArrowRight = (p) => (
  <Svg {...p}>
    <path d="M4.4 12h15.2" />
    <path d="m14 6.4 5.6 5.6-5.6 5.6" />
  </Svg>
)

/** 更多操作（三个点）：行尾的删除菜单 */
export const IconDots = (p) => (
  <Svg {...p} solid>
    <circle cx="5.6" cy="12" r="1.7" />
    <circle cx="12" cy="12" r="1.7" />
    <circle cx="18.4" cy="12" r="1.7" />
  </Svg>
)


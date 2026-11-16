import { useCallback, useMemo, useRef, useState } from 'react'
import { bulkImportJobs, clearJobs, notify } from '../../api/client'
import { parseClipboard, readFile } from '../../lib/xlsx'
import { todayStr } from '../../lib/jobFormat'
import JobDialog from './JobDialog'
import { IconCheck, IconRefresh, IconUpload, IconWarn } from '../icons'

/**
 * 导入向导（登录即可使用）：选数据源 → 核对字段映射 → 看结果。
 *
 * 三条必须守住的规则（每一条都对应一次踩坑）：
 *
 * 1. **只导入 8 个岗位信息字段**（企业名称 / 雇主类型 / 行业 / 毕业届别 / 工作地点 /
 *    专业要求 / 招聘岗位 / 投递链接）。「投递进度」「更新时间」由系统决定——
 *    进度一律记为「未投递」、更新时间记为导入当天。即使源表里有同名列也**完全不读取**：
 *    否则从同学那儿拿到的汇总表会把别人的投递状态写进你的记录。
 *    「公告链接」与「备注」已按需求从导入字段里移除，源表里有这两列也不读。
 * 2. **表头自动匹配，但不会互相抢占列**。先做精确匹配（含同义词库），
 *    再做包含匹配；匹配上的下拉框会高亮，不匹配的留空「不导入」也不影响导入。
 * 3. **同义词库覆盖常见写法**（公司名称 / 单位 / 职位 / 工作城市…），
 *    因为大家手上的表从来不是同一套表头。
 *
 * 解析在前端完成（`lib/xlsx.js`，零依赖）：服务端只接受结构化 JSON，
 * 这样「一份解析器」既服务 .xlsx、.csv 与粘贴三条路径，也不必为读一张表引入 Python 的表格库。
 */

/* 导入字段定义：syn 为表头同义词库，用于自动匹配 */
const IMPORT_FIELDS = [
  { key: 'company', label: '企业名称', required: true, syn: ['企业名称', '公司名称', '单位名称', '企业', '公司', '单位', '招聘单位', '雇主', '公司名'] },
  { key: 'nature', label: '雇主类型', syn: ['雇主类型', '企业性质', '公司性质', '单位性质', '性质', '企业类型', '单位类型', '公司类型'] },
  { key: 'industry', label: '行业', syn: ['行业', '所属行业', '行业类别', '行业领域', '业务领域', '赛道', '板块'] },
  { key: 'audience', label: '毕业届别', syn: ['毕业届别', '招聘对象', '招聘人群', '面向人群', '招聘范围', '对象', '届别', '届次', '学历', '学历要求'] },
  { key: 'locations', label: '工作地点', multi: true, syn: ['工作地点', '工作城市', '办公地点', '工作地', '地点', '城市', '所在地', '地区', 'base'] },
  { key: 'majors', label: '专业要求', multi: true, syn: ['专业要求', '需求专业', '专业方向', '相关专业', '专业'] },
  { key: 'positions', label: '招聘岗位', multi: true, syn: ['招聘岗位', '岗位名称', '职位名称', '投递岗位', '招聘职位', '岗位', '职位', '岗位方向'] },
  { key: 'applyUrl', label: '投递链接', syn: ['投递链接', '网申链接', '报名链接', '申请链接', '投递网址', '申请网址', '投递'] },
]

/** 表头归一化：去掉空白与标点，便于模糊匹配（「公司名称（全称）」也能命中） */
function normHeader(s) {
  return String(s == null ? '' : s)
    .toLowerCase()
    .replace(/[\s\u3000]/g, '')
    .replace(/[（）()【】[\]《》<>:：*·、,，.。/\\|｜'"“”‘’\-—_+]/g, '')
}

/** 把「北京、上海 / 深圳」这类文本拆成数组（与后端 split_tags 同一套分隔符） */
function splitTokens(text) {
  return String(text || '')
    .split(/[、,，;；/|｜\n\r\t]+/)
    .map((s) => s.trim())
    .filter(Boolean)
}

export default function JobImportDialog({ onClose, onDone }) {
  const [step, setStep] = useState(1)
  const [sheets, setSheets] = useState([])
  const [fileName, setFileName] = useState('')
  const [sheetIdx, setSheetIdx] = useState(0)
  const [hasHeader, setHasHeader] = useState(true)
  const [map, setMap] = useState({})
  const [autoMap, setAutoMap] = useState({})
  const [pasteMode, setPasteMode] = useState(false)
  const [pasteText, setPasteText] = useState('')
  const [mode, setMode] = useState('append')
  // 去重开关：**默认关闭**（导入岗位数据默认不去重）。
  // 校招汇总表里同一家企业因不同批次 / 不同岗位方向重复出现是常态，
  // 默认去重会把后面那些行静默吃掉，而用户只看到「跳过 N 条」，很难判断丢了什么。
  const [dedupe, setDedupe] = useState(false)
  const [reading, setReading] = useState(false)
  const [running, setRunning] = useState(false)
  const [result, setResult] = useState(null)
  const [dragOver, setDragOver] = useState(false)
  const fileRef = useRef(null)

  const currentRows = sheets[sheetIdx]?.rows || []

  const headers = useMemo(() => {
    if (!currentRows.length) return []
    if (hasHeader) return currentRows[0].map((h, i) => String(h || '').trim() || `第 ${i + 1} 列`)
    return currentRows[0].map((_h, i) => `第 ${i + 1} 列`)
  }, [currentRows, hasHeader])

  const dataRows = useMemo(() => (hasHeader ? currentRows.slice(1) : currentRows), [currentRows, hasHeader])

  /** 自动匹配表头 → 字段：先精确匹配，再包含匹配，避免两列互相抢占 */
  const buildAutoMap = useCallback(
    (heads) => {
      const next = {}
      const used = {}
      IMPORT_FIELDS.forEach((f) => {
        next[f.key] = -1
      })

      IMPORT_FIELDS.forEach((f) => {
        for (let i = 0; i < heads.length; i++) {
          if (used[i]) continue
          const h = normHeader(heads[i])
          if (!h) continue
          if (f.syn.some((s) => normHeader(s) === h)) {
            next[f.key] = i
            used[i] = true
            return
          }
        }
      })

      IMPORT_FIELDS.forEach((f) => {
        if (next[f.key] >= 0) return
        for (let i = 0; i < heads.length; i++) {
          if (used[i]) continue
          const h = normHeader(heads[i])
          if (!h) continue
          if (
            f.syn.some((s) => {
              const ns = normHeader(s)
              return ns && (h.includes(ns) || ns.includes(h))
            })
          ) {
            next[f.key] = i
            used[i] = true
            return
          }
        }
      })

      setMap(next)
      setAutoMap({ ...next })
    },
    []
  )

  /**
   * 一行源数据 → 一条待导入记录（只读 8 个字段，进度与时间由服务端决定）。
   *
   * 「公告链接」与「备注」**已按需求从导入字段里移除**：源表里即使有这两列，
   * 也不会被读进来（它们不在 IMPORT_FIELDS 里，map 里根本没有对应位置）。
   * 库里这两个字段依然存在、历史数据也不动，只是不再从导入这条路写入了。
   */
  const toRecord = useCallback(
    (cells) => {
      const cell = (key) => {
        const i = map[key]
        if (i == null || i < 0) return ''
        return String(cells[i] == null ? '' : cells[i]).trim()
      }
      return {
        company: cell('company'),
        nature: cell('nature'),
        industry: cell('industry'),
        audience: cell('audience'),
        locations: splitTokens(cell('locations')),
        majors: splitTokens(cell('majors')),
        positions: splitTokens(cell('positions')),
        apply_url: cell('applyUrl'),
      }
    },
    [map]
  )

  /** 组装待导入记录：跳过空行、没有企业名的行、以及混在数据里的重复表头行 */
  const built = useMemo(() => {
    const headerSet = {}
    headers.forEach((h) => {
      headerSet[normHeader(h)] = true
    })
    const records = []
    let headerLike = 0
    dataRows.forEach((cells) => {
      if (!cells || !cells.some((x) => String(x || '').trim())) return
      const rec = toRecord(cells)
      if (!rec.company) return
      if (headerSet[normHeader(rec.company)]) {
        headerLike += 1
        return
      }
      records.push(rec)
    })
    return { records, headerLike, total: dataRows.length }
  }, [dataRows, headers, toRecord])

  const handleFile = async (file) => {
    if (!file) return
    setReading(true)
    try {
      const parsed = await readFile(file)
      const list = parsed?.sheets?.length ? parsed.sheets : []
      if (!list.length) {
        notify('这个文件里没有可读的数据表', 'warn')
        setReading(false)
        return
      }
      setSheets(list)
      setSheetIdx(0)
      setPasteMode(false)
      setFileName(`${file.name} · 共 ${Math.max(0, (list[0].rows || []).length - 1)} 行数据`)
      setStep(2)
      buildAutoMap((list[0].rows?.[0] || []).map((h, i) => String(h || '').trim() || `第 ${i + 1} 列`))
    } catch (err) {
      notify(err?.message || '文件解析失败', 'error', 4200)
    } finally {
      setReading(false)
    }
  }

  const applyPaste = () => {
    const text = pasteText.trim()
    if (!text) {
      notify('请先在 Excel 里选中区域复制，再粘贴到这里', 'warn')
      return
    }
    const rows = parseClipboard(text)
    if (!rows.length) {
      notify('粘贴的内容里没有可解析的表格数据', 'warn')
      return
    }
    const sheet = { name: '粘贴的数据', rows }
    setSheets([sheet])
    setSheetIdx(0)
    setFileName(`粘贴的数据 · 共 ${Math.max(0, rows.length - 1)} 行数据`)
    buildAutoMap(rows[0].map((h, i) => String(h || '').trim() || `第 ${i + 1} 列`))
    setStep(2)
  }

  /** 切换工作表 / 表头开关后重新自动匹配（列结构变了，映射必须重算） */
  const changeSheet = (idx) => {
    setSheetIdx(idx)
    const rows = sheets[idx]?.rows || []
    const heads = hasHeader
      ? (rows[0] || []).map((h, i) => String(h || '').trim() || `第 ${i + 1} 列`)
      : (rows[0] || []).map((_h, i) => `第 ${i + 1} 列`)
    buildAutoMap(heads)
    setFileName(`${sheets[idx]?.name || ''} · 共 ${Math.max(0, rows.length - 1)} 行数据`)
  }

  const toggleHeader = (checked) => {
    setHasHeader(checked)
    const rows = sheets[sheetIdx]?.rows || []
    const heads = checked
      ? (rows[0] || []).map((h, i) => String(h || '').trim() || `第 ${i + 1} 列`)
      : (rows[0] || []).map((_h, i) => `第 ${i + 1} 列`)
    buildAutoMap(heads)
  }

  const run = async () => {
    if (map.company < 0) {
      notify('请先指定「企业名称」对应的列', 'warn')
      return
    }
    const list = built.records.map((r) => ({
      /* 只提交岗位信息本身：progress / 更新时间 由服务端决定；
         公告链接与备注已从导入字段中移除，这里不再提交 */
      company: r.company,
      nature: r.nature,
      industry: r.industry,
      audience: r.audience,
      locations: r.locations,
      majors: r.majors,
      positions: r.positions,
      apply_url: r.apply_url,
    }))
    if (!list.length) {
      notify('没有解析出可导入的数据（企业名称为空的行会被跳过）', 'warn')
      return
    }
    // 替换模式是**破坏性**操作：会清空岗位库并连带清除所有人的投递进度，
    // 因此即便界面上已经用红字说明过，执行前仍然再确认一次（与「清空全部岗位」同一口径）。
    if (mode === 'replace') {
      const ok = window.confirm(
        `确定要替换全部数据吗？\n\n这会先清空现有岗位库，并清除所有人在这些岗位上的投递进度（不可撤销）。\n随后导入本次解析出的 ${list.length} 条岗位。`
      )
      if (!ok) return
    }

    setRunning(true)
    try {
      let added = 0
      let skipped = 0
      if (mode === 'replace') {
        // 替换 = 清空后再导入（清空会连带清除所有人的相关进度，因此界面必须先说清）。
        // 这里**显式传 false**：刚清空过，去重没有意义，而且会吃掉源表内部的重复行。
        await clearJobs()
        const res = await bulkImportJobs(list, false)
        added = res?.added || list.length
        skipped = res?.skipped || 0
      } else {
        const res = await bulkImportJobs(list, dedupe)
        added = res?.added || 0
        skipped = res?.skipped || 0
      }
      setResult({ added, skipped, total: list.length, headerLike: built.headerLike })
      setStep(3)
      await onDone?.()
    } catch (err) {
      notify(err?.message || '导入失败', 'error', 4200)
    } finally {
      setRunning(false)
    }
  }

  const remaining = built.records.length

  return (
    <JobDialog
      title="导入岗位数据"
      subtitle={
        step === 1
          ? '支持 .xlsx / .csv / 从 Excel 直接粘贴，自动识别表头'
          : step === 2
            ? fileName
            : '导入完成'
      }
      wide
      onClose={running ? () => {} : onClose}
      footer={
        <div className="row row--between gap-12" style={{ flexWrap: 'wrap' }}>
          <span className="mute-2" style={{ fontSize: 12 }}>
            只导入 8 个岗位信息字段；投递进度与更新时间由系统决定
          </span>
          <div className="row gap-8">
            {step === 2 && (
              <button type="button" className="btn btn--ghost btn--sm" onClick={() => setStep(1)} disabled={running}>
                上一步
              </button>
            )}
            {step === 2 && (
              <button type="button" className="btn btn--primary btn--sm" onClick={run} disabled={running || map.company < 0}>
                {running ? <span className="spinner" /> : null}
                {running ? '导入中…' : `导入 ${remaining} 条`}
              </button>
            )}
            {step !== 2 && (
              <button type="button" className="btn btn--primary btn--sm" onClick={onClose} disabled={running}>
                {step === 3 ? '完成' : '取消'}
              </button>
            )}
          </div>
        </div>
      }
    >
      {/* ---------------- 第一步：选数据源 ---------------- */}
      {step === 1 && (
        <div className="jh-import">
          {!pasteMode ? (
            <>
              <label
                className={`jh-dropzone ${dragOver ? 'over' : ''}`}
                onDragOver={(e) => {
                  e.preventDefault()
                  setDragOver(true)
                }}
                onDragLeave={() => setDragOver(false)}
                onDrop={(e) => {
                  e.preventDefault()
                  setDragOver(false)
                  handleFile(e.dataTransfer?.files?.[0])
                }}
              >
                <input
                  ref={fileRef}
                  type="file"
                  accept=".xlsx,.csv,.txt,.tsv"
                  className="sr-only"
                  onChange={(e) => handleFile(e.target.files?.[0])}
                />
                {reading ? <span className="spinner spinner--primary" /> : <IconUpload size={26} />}
                <span className="jh-dropzone-title">{reading ? '正在解析文件…' : '点击选择文件，或把表格拖到这里'}</span>
                <span className="jh-dropzone-sub">.xlsx（Excel 2007+）/ .csv / .txt / .tsv</span>
              </label>
              <p className="jh-hint">
                <IconWarn size={14} className="inline-ic" />
                不支持旧版 <strong>.xls</strong>（二进制格式）与加密工作簿：
                请在 Excel 里「另存为」为 .xlsx 或「CSV UTF-8（逗号分隔）」后重试。
              </p>
              <button type="button" className="link-btn" onClick={() => setPasteMode(true)}>
                没有文件？直接从 Excel 复制粘贴 →
              </button>
            </>
          ) : (
            <>
              <p className="jh-hint">
                在 Excel 里选中区域按 <kbd className="jh-kbd">Ctrl</kbd> + <kbd className="jh-kbd">C</kbd>，
                粘贴到下面的框里（含表头）。不用先存成文件。
              </p>
              <textarea
                className="textarea jh-paste"
                value={pasteText}
                onChange={(e) => setPasteText(e.target.value)}
                placeholder={'企业名称\t雇主类型\t行业\t毕业届别\t工作地点\t专业要求\t招聘岗位\t投递链接'}
                aria-label="粘贴表格数据"
              />
              <div className="row gap-8" style={{ marginTop: 12 }}>
                <button type="button" className="btn btn--primary btn--sm" onClick={applyPaste}>
                  解析粘贴内容
                </button>
                <button type="button" className="btn btn--ghost btn--sm" onClick={() => setPasteMode(false)}>
                  改用文件导入
                </button>
                <button type="button" className="link-btn" onClick={() => setPasteText('')}>
                  清空
                </button>
              </div>
            </>
          )}
        </div>
      )}

      {/* ---------------- 第二步：字段映射 + 预览 ---------------- */}
      {step === 2 && (
        <div className="jh-import">
          <div className="jh-map-controls">
            {sheets.length > 1 && (
              <label className="field jh-field-inline">
                <span className="field-label">工作表</span>
                <select className="select" value={sheetIdx} onChange={(e) => changeSheet(Number(e.target.value))}>
                  {sheets.map((s, i) => (
                    <option key={`${s.name}-${i}`} value={i}>
                      {s.name}（{s.rows.length} 行）
                    </option>
                  ))}
                </select>
              </label>
            )}
            <label className="jh-check">
              <input type="checkbox" checked={hasHeader} onChange={(e) => toggleHeader(e.target.checked)} />
              <span>首行是表头</span>
            </label>
            <span className="mute-2" style={{ fontSize: 12 }}>
              解析出 <strong>{remaining}</strong> 条
              {built.headerLike > 0 ? `（跳过 ${built.headerLike} 行重复表头）` : ''}
            </span>
          </div>

          <div className="jh-map-grid">
            {IMPORT_FIELDS.map((f) => {
              const isAuto = map[f.key] >= 0 && autoMap[f.key] === map[f.key]
              return (
                <div className="jh-map-row" key={f.key}>
                  <label className="jh-map-label" htmlFor={`jh-map-${f.key}`}>
                    {f.label}
                    {f.required && <span className="jh-req">*</span>}
                  </label>
                  <select
                    id={`jh-map-${f.key}`}
                    className={`select jh-map-select ${isAuto ? 'is-auto' : ''} ${map[f.key] < 0 ? 'is-missing' : ''}`}
                    value={String(map[f.key] ?? -1)}
                    onChange={(e) => setMap((prev) => ({ ...prev, [f.key]: Number(e.target.value) }))}
                  >
                    <option value="-1">— 不导入 —</option>
                    {headers.map((h, i) => (
                      <option key={`${h}-${i}`} value={i}>
                        {h}
                      </option>
                    ))}
                  </select>
                </div>
              )
            })}
          </div>

          <div className="jh-import-mode">
            <label className="jh-check">
              <input type="radio" name="jh-mode" checked={mode === 'append'} onChange={() => setMode('append')} />
              <span>追加到现有数据</span>
            </label>
            <label className="jh-check">
              <input type="radio" name="jh-mode" checked={mode === 'replace'} onChange={() => setMode('replace')} />
              <span>替换全部数据（先清空现有岗位与所有人的投递进度）</span>
            </label>
            {mode === 'append' && (
              <>
                <label className="jh-check">
                  <input type="checkbox" checked={dedupe} onChange={(e) => setDedupe(e.target.checked)} />
                  <span>跳过已存在的企业（按企业名称去重）</span>
                </label>
                {/* 两种状态各自把后果说清：默认「不去重」时最需要提醒的是
                    「重复导入同一份文件会产生重复数据」，这比一个勾选框本身更重要。 */}
                <p className="jh-hint jh-hint--info" style={{ marginTop: 2 }}>
                  {dedupe
                    ? '已开启去重：企业名称与现有岗位库重复的行会被跳过（同一批内部也去重）。'
                    : '默认不去重：源表里有多少行就导入多少行 —— 同一家企业因不同批次 / 岗位方向重复出现时都会各自入库；重复导入同一份文件会产生重复数据。'}
                </p>
              </>
            )}
          </div>

          <div className="jh-preview">
            <div className="jh-preview-head">
              预览前 {Math.min(3, built.records.length)} 条 · 投递进度与更新时间由系统写入
            </div>
            <div className="jh-tablewrap">
              <table className="jh-table jh-table--preview">
                <thead>
                  <tr>
                    {IMPORT_FIELDS.map((f) => (
                      <th key={f.key}>{f.label}</th>
                    ))}
                    <th>投递进度</th>
                    <th>更新时间</th>
                  </tr>
                </thead>
                <tbody>
                  {built.records.length === 0 ? (
                    <tr>
                      <td colSpan={IMPORT_FIELDS.length + 2} className="center mute-2">
                        {map.company < 0 ? '请先指定「企业名称」对应的列' : '没有可预览的数据'}
                      </td>
                    </tr>
                  ) : (
                    built.records.slice(0, 3).map((r, i) => (
                      <tr key={i}>
                        <td>{r.company}</td>
                        <td>{r.nature || '—'}</td>
                        <td>{r.industry || '—'}</td>
                        <td>{r.audience || '—'}</td>
                        <td>{r.locations.join('、') || '—'}</td>
                        <td>{r.majors.join('、') || '—'}</td>
                        <td>{r.positions.join('、') || '—'}</td>
                        <td className="jh-ellip">{r.apply_url || '—'}</td>
                        <td>未投递</td>
                        <td>{todayStr()}</td>
                      </tr>
                    ))
                  )}
                </tbody>
              </table>
            </div>
          </div>
          <p className="jh-hint">
            高亮的映射是<strong>自动匹配</strong>的结果，可以手动改。源表里即使有「投递进度」「更新时间」
            同名列，也会被完全忽略——避免从别处拿到的汇总表污染你自己的投递记录。
          </p>
        </div>
      )}

      {/* ---------------- 第三步：结果 ---------------- */}
      {step === 3 && result && (
        <div className="jh-import">
          <div className="jh-result">
            <IconCheck size={28} style={{ color: 'var(--fg-ok)' }} />
            <div className="jh-result-title">导入完成</div>
            <ul className="jh-result-list">
              <li>
                新增 <strong className="mono">{result.added}</strong> 条
              </li>
              <li>
                跳过 <strong className="mono">{result.skipped}</strong> 条
                <span className="mute-2">（企业名为空或已存在）</span>
              </li>
              <li>
                本次解析 <strong className="mono">{result.total}</strong> 条
              </li>
            </ul>
            <p className="jh-hint">
              导入的岗位「投递进度」一律记为「未投递」——投递状态只能由你自己在列表里维护。
              要真正备份 / 迁移**岗位库**，请备份服务端的数据库文件：
              导入接口只认 8 个岗位信息字段（不含公告链接与备注），做不到「导出再导入就还原」。
              需要带走自己的投递记录时，用「投递统计」里的导出统计 Excel。
            </p>
            <div className="row gap-8" style={{ justifyContent: 'center', marginTop: 12 }}>
              <button type="button" className="btn btn--primary btn--sm" onClick={onClose}>
                完成
              </button>
              <button
                type="button"
                className="btn btn--ghost btn--sm"
                onClick={() => {
                  setResult(null)
                  setStep(1)
                }}
              >
                <IconRefresh size={15} /> 再导入一批
              </button>
            </div>
          </div>
        </div>
      )}
    </JobDialog>
  )
}

import { useEffect, useMemo, useRef, useState } from 'react'
import {
  JOB_FILTERS,
  JOB_SEGMENTS,
  JOB_STATUS_DEFAULT,
  isApplied,
  jobStageOf,
  jobTypeOf,
  matchFilterValue,
  searchableText,
} from '../../constants/jobStatus'
import { daysOld, fullUrl, relativeDay } from '../../lib/jobFormat'
import JobStatusBadge from './JobStatusBadge'
import JobPager, { JOB_PAGE_SIZE } from './JobPager'
import {
  IconArrowRight,
  IconBuilding,
  IconChevron,
  IconDots,
  IconFilter,
  IconInbox,
  IconRefresh,
  IconSearch,
  IconTrash,
  IconUpload,
  IconX,
} from '../icons'

/**
 * 汇总列表（岗位信息主体）。
 *
 * 交互口径逐条都有理由：
 *
 * - **点击表格任意位置都不会弹出编辑器**。岗位信息是只读的（唯一来源是导入），
 *   行点击不做任何事，避免误触改数据。改数据只有两个入口：
 *   行内的进度徽章（改自己的进度）与行尾菜单（删岗位）。
 * - **六个筛选维度都是多选**：同一维度内为「或」，不同维度之间为「与」。
 * - **搜索框是全文匹配**：企业 / 性质 / 行业 / 对象 / 地点 / 专业 / 岗位 / 备注 / 进度。
 *   （备注虽然不再单独占一列，但仍参与搜索 —— 搜内推码依然能命中对应岗位。）
 * - **维护入口对所有登录用户开放**（导入 / 载入示例 / 清空 / 删除）：岗位库没有单独的
 *   管理员体系，权限由后端那句「要求登录」表达，前端不再按身份藏按钮。
 *   （曾经的「导出全部岗位」已按需求下线：岗位库动辄上百条，导出整库既容易把
 *   别人的共享数据带出产品，也不是这个页面真正要解决的问题 —— 真正需要带走的是
 *   「我投了哪些」，那个入口在「投递统计 → 导出统计 Excel」。）
 *
 * 列表上方的「昨日更新 N 家」来自服务端的 `postedYesterday`（**全库口径，不随筛选变化**），
 * 不要在组件里用 `visible` / `records` 现算：一加筛选数字就掉，会被读成「今天只更新了几家」。
 *
 * 列宽统一在 `pages/job-hub.css` 的 `.jh-table { --jh-col-*-w }` 变量块里调。
 */
export default function JobListPanel({
  records,
  postedYesterday = 0,
  busy,
  onPickStatus,
  onImport,
  onSeed,
  onClearAll,
  onDelete,
}) {
  const [query, setQuery] = useState('')
  const [segment, setSegment] = useState('all')
  const [filters, setFilters] = useState(() => {
    const init = {}
    JOB_FILTERS.forEach((f) => {
      init[f.key] = new Set()
    })
    return init
  })
  const [openFilter, setOpenFilter] = useState('')
  const [filterQuery, setFilterQuery] = useState('')
  const [sort, setSort] = useState({ key: 'posted_at', dir: 'desc' })
  const [menuFor, setMenuFor] = useState('')
  const [page, setPage] = useState(1)
  const searchRef = useRef(null)

  /* 删除岗位前的二次确认。
     岗位库现在是**全员共享、全员可维护**：删一条会连带清掉所有人在该岗位上的投递进度，
     影响面不止自己。以前只有一个人能删，漏确认的代价可控；现在任何一个登录用户
     误点一下就是「大家的记录一起没了」，因此这里必须再确认一次
     （与「清空岗位库」在导入向导里的同一口径）。 */
  const confirmDelete = (record) => {
    const label = record?.company || '这条岗位'
    const ok = window.confirm(
      `删除「${label}」？\n\n` +
        '岗位库是全员共享的：删除会同时清除**所有人在该岗位上的投递进度**，且无法撤销。'
    )
    if (ok) onDelete(record)
  }

  /* ⌘K / Ctrl+K 聚焦搜索：数据密集的页面上，键盘用户最需要的是「直接开始找」 */
  useEffect(() => {
    const onKey = (e) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') {
        e.preventDefault()
        searchRef.current?.focus()
      }
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [])

  /* 行尾菜单点外面就收起（菜单本身是绝对定位的浮层） */
  useEffect(() => {
    if (!menuFor) return undefined
    const off = () => setMenuFor('')
    document.addEventListener('click', off)
    return () => document.removeEventListener('click', off)
  }, [menuFor])

  /** 每个筛选维度的可选项：从当前数据现算，并按出现次数排序（常用的在前） */
  const options = useMemo(() => {
    const map = {}
    JOB_FILTERS.forEach((f) => {
      const counts = new Map()
      records.forEach((r) => {
        if (f.multi) {
          ;(r[f.key] || []).forEach((v) => v && counts.set(v, (counts.get(v) || 0) + 1))
        } else {
          const v = r[f.key]
          if (v) counts.set(v, (counts.get(v) || 0) + 1)
        }
      })
      map[f.key] = [...counts.entries()]
        .map(([value, count]) => ({ value, count }))
        .sort((a, b) => (b.count !== a.count ? b.count - a.count : a.value.localeCompare(b.value, 'zh-Hans-CN')))
    })
    return map
  }, [records])

  /* 数据变了以后，把已经不存在的选中值清掉，否则会出现「筛完是空的却查不出原因」 */
  useEffect(() => {
    setFilters((prev) => {
      let changed = false
      const next = { ...prev }
      JOB_FILTERS.forEach((f) => {
        const valid = new Set((options[f.key] || []).map((o) => o.value))
        const kept = new Set([...prev[f.key]].filter((v) => valid.has(v)))
        if (kept.size !== prev[f.key].size) {
          next[f.key] = kept
          changed = true
        }
      })
      return changed ? next : prev
    })
  }, [options])

  const visible = useMemo(() => {
    const q = query.trim().toLowerCase()
    const list = records.filter((r) => {
      if (segment !== 'all' && jobTypeOf(r.progress) !== segment) return false
      if (q && !searchableText(r).includes(q)) return false
      return JOB_FILTERS.every((f) => matchFilterValue(r[f.key], filters[f.key]))
    })
    const dir = sort.dir === 'asc' ? 1 : -1
    return list.sort((a, b) => {
      let va
      let vb
      if (sort.key === 'progress') {
        va = jobStageOf(a.progress) * 10 + (jobTypeOf(a.progress) === 'fail' ? -1 : 0)
        vb = jobStageOf(b.progress) * 10 + (jobTypeOf(b.progress) === 'fail' ? -1 : 0)
        return (va - vb) * dir
      }
      va = sort.key === 'company' ? a.company || '' : a.posted_at || ''
      vb = sort.key === 'company' ? b.company || '' : b.posted_at || ''
      return String(va).localeCompare(String(vb), 'zh-Hans-CN') * dir
    })
  }, [records, query, segment, filters, sort])

  /* ---------- 分页（每页 30 条） ----------
     三个状态的更新点：筛选 / 搜索 / 分段 / 排序一变就回到第 1 页
     （否则用户在第 5 页改完筛选会看到一个空列表，还以为是没数据）；
     数据量变小（例如删掉一批岗位）时把页码夹回最后一页。 */
  const totalPages = Math.max(1, Math.ceil(visible.length / JOB_PAGE_SIZE))
  useEffect(() => {
    setPage(1)
  }, [query, segment, filters, sort])
  useEffect(() => {
    setPage((p) => Math.min(p, totalPages))
  }, [totalPages])
  const currentPage = Math.min(page, totalPages)
  const pageItems = useMemo(
    () => visible.slice((currentPage - 1) * JOB_PAGE_SIZE, currentPage * JOB_PAGE_SIZE),
    [visible, currentPage]
  )

  const chipList = useMemo(() => {
    const chips = []
    JOB_FILTERS.forEach((f) => {
      filters[f.key].forEach((value) => chips.push({ key: f.key, value, label: f.label }))
    })
    if (segment !== 'all') {
      chips.push({ key: 'seg', value: segment, label: JOB_SEGMENTS.find((s) => s.key === segment)?.label || '' })
    }
    return chips
  }, [filters, segment])

  const dropChip = (chip) => {
    if (chip.key === 'seg') setSegment('all')
    else {
      setFilters((prev) => {
        const next = new Set(prev[chip.key])
        next.delete(chip.value)
        return { ...prev, [chip.key]: next }
      })
    }
  }

  const clearAll = () => {
    setQuery('')
    setSegment('all')
    setOpenFilter('')
    setFilters(() => {
      const init = {}
      JOB_FILTERS.forEach((f) => {
        init[f.key] = new Set()
      })
      return init
    })
  }

  const toggleFilterValue = (key, value) => {
    setFilters((prev) => {
      const next = new Set(prev[key])
      if (next.has(value)) next.delete(value)
      else next.add(value)
      return { ...prev, [key]: next }
    })
  }

  const toggleSort = (key) =>
    setSort((prev) => ({ key, dir: prev.key === key && prev.dir === 'desc' ? 'asc' : 'desc' }))

  const hasFilter = chipList.length > 0 || !!query.trim()
  const filteredOptions = openFilter
    ? (options[openFilter] || []).filter((o) =>
        filterQuery.trim() ? o.value.toLowerCase().includes(filterQuery.trim().toLowerCase()) : true
      )
    : []

  const activeFilterDef = JOB_FILTERS.find((f) => f.key === openFilter)
  const emptyLibrary = records.length === 0

  /* 行内进度徽章：列表页与移动端卡片共用 */
  const progressCell = (r) => (
    <JobStatusBadge status={r.progress} interactive onClick={() => onPickStatus(r)} size="sm" />
  )

  /** 投递链接按钮：没填链接时给出**不可点的占位**而不是隐藏（列宽不跳） */
  const linkBtn = (url, label, Icon) => {
    if (!url) {
      return (
        <span className="jh-linkbtn is-off" title={`未填写${label}链接`} aria-hidden="true">
          <Icon size={15} />
        </span>
      )
    }
    return (
      <a
        className="jh-linkbtn"
        href={fullUrl(url)}
        target="_blank"
        rel="noopener noreferrer"
        title={`${label}：${url}`}
        aria-label={`打开${label}链接`}
      >
        <Icon size={15} />
      </a>
    )
  }

  return (
    <>
      {/* ---------------- 昨日更新标注 ----------------
          数据源是服务端的 `posted_yesterday`（按**本地时区**判定「昨天」，口径见
          job_store.count_posted_yesterday），**不随筛选 / 搜索变化**：它回答的是
          「昨天岗位库新增了多少家」，而不是「筛出来的这些里有几条是昨天的」。
          因此这里刻意不去用 `visible`/`records` 现算 —— 那样一加筛选数字就掉，
          用户会误以为昨天只更新了几家。
          统计「昨天」而不是「今天」：今天的数据还在陆续导入、数字会一直跳，
          昨天是一段已经走完、不会再变的完整区间，更能说明岗位库最近有没有在维护。
          0 也照常显示：一个诚实的 0 比「什么都没说」更有用（用户能确认页面没坏）。 */}
      <div className={`jh-daily ${postedYesterday > 0 ? 'is-hot' : ''}`} role="status">
        <span className="jh-daily-dot" aria-hidden="true" />
        <span className="jh-daily-label">昨日更新</span>
        <strong className="jh-daily-num">{postedYesterday}</strong>
        <span className="jh-daily-unit">家</span>
        <span className="jh-daily-hint">
          {postedYesterday > 0 ? '昨天新入库的岗位' : '昨天没有新岗位入库'}
        </span>
      </div>

      {/* ---------------- 工具栏 + 筛选 ---------------- */}
      <div className="jh-toolbar card card--flat">
        <div className="jh-toolbar-row">
          <div className="jh-search">
            <IconSearch size={16} className="jh-search-ic" />
            <input
              ref={searchRef}
              className="input jh-search-input"
              type="search"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="搜索企业 / 行业 / 岗位 / 专业 / 地点"
              aria-label="搜索岗位"
              autoComplete="off"
            />
          </div>
          <div className="jh-seg" role="tablist" aria-label="按投递状态筛选">
            {JOB_SEGMENTS.map((s) => (
              <button
                key={s.key}
                type="button"
                role="tab"
                aria-selected={segment === s.key}
                className={`jh-seg-btn ${segment === s.key ? 'on' : ''}`}
                onClick={() => setSegment(s.key)}
              >
                {s.label}
              </button>
            ))}
          </div>
        </div>

        <div className="jh-filter-row">
          <span className="jh-filter-caption">
            <IconFilter size={15} /> 筛选
          </span>
          {JOB_FILTERS.map((f) => {
            const count = filters[f.key].size
            const on = openFilter === f.key
            return (
              <button
                key={f.key}
                type="button"
                className={`jh-fbtn ${count ? 'has' : ''} ${on ? 'on' : ''}`}
                aria-expanded={on}
                aria-haspopup="true"
                disabled={!options[f.key]?.length}
                title={!options[f.key]?.length ? `暂无「${f.label}」数据` : undefined}
                onClick={() => {
                  setOpenFilter(on ? '' : f.key)
                  setFilterQuery('')
                }}
              >
                {f.label}
                {count > 0 && <span className="jh-fbtn-count">{count}</span>}
                <IconChevron size={13} />
              </button>
            )
          })}
          {hasFilter && (
            <button type="button" className="btn btn--ghost btn--sm jh-clear" onClick={clearAll}>
              清除筛选
            </button>
          )}
        </div>

        {/* 展开的维度选项：用**胶囊多选**而不是下拉复选，移动端更好点 */}
        {activeFilterDef && (
          <div className="jh-option-panel">
            <div className="jh-option-head">
              <strong>{activeFilterDef.label}</strong>
              <span className="mute-2" style={{ fontSize: 12 }}>
                同一维度内为「或」，不同维度之间为「与」
              </span>
              <div className="row gap-8" style={{ marginLeft: 'auto' }}>
                <input
                  className="input jh-option-search"
                  type="search"
                  value={filterQuery}
                  onChange={(e) => setFilterQuery(e.target.value)}
                  placeholder="搜索选项…"
                  aria-label={`搜索${activeFilterDef.label}选项`}
                />
                <button
                  type="button"
                  className="link-btn"
                  onClick={() => setFilters((prev) => ({ ...prev, [activeFilterDef.key]: new Set((options[activeFilterDef.key] || []).map((o) => o.value)) }))}
                >
                  全选
                </button>
                <button
                  type="button"
                  className="link-btn"
                  onClick={() => setFilters((prev) => ({ ...prev, [activeFilterDef.key]: new Set() }))}
                >
                  清除
                </button>
              </div>
            </div>
            <div className="jh-option-list">
              {filteredOptions.length === 0 ? (
                <span className="mute-2" style={{ fontSize: 12 }}>
                  没有匹配的选项
                </span>
              ) : (
                filteredOptions.map((o) => {
                  const on = filters[activeFilterDef.key].has(o.value)
                  return (
                    <button
                      key={o.value}
                      type="button"
                      className={`jh-opt-chip ${on ? 'on' : ''}`}
                      aria-pressed={on}
                      onClick={() => toggleFilterValue(activeFilterDef.key, o.value)}
                    >
                      {o.value}
                      <span className="jh-opt-count">{o.count}</span>
                    </button>
                  )
                })
              )}
            </div>
          </div>
        )}

        {chipList.length > 0 && (
          <div className="jh-chips">
            {chipList.map((c) => (
              <button key={`${c.key}-${c.value}`} type="button" className="jh-chip" onClick={() => dropChip(c)}>
                <span className="mute-2">{c.label}</span> {c.value}
                <IconX size={12} />
              </button>
            ))}
          </div>
        )}
      </div>

      {/* ---------------- 表格（桌面） / 卡片（窄屏） ---------------- */}
      {emptyLibrary ? (
        <div className="card mt-16">
          <div className="empty">
            <IconInbox size={30} style={{ color: 'var(--text-mute)' }} />
            <div className="empty-title">岗位库还是空的</div>
            <div className="empty-desc">
              可以先「载入示例数据」看看长什么样，或直接导入自己手上的 Excel / CSV；
              导入后这份汇总对所有登录用户可见。
            </div>
            <div className="row gap-12 mt-16" style={{ justifyContent: 'center', flexWrap: 'wrap' }}>
              <button className="btn btn--primary" onClick={onImport} disabled={busy}>
                <IconUpload size={16} /> 导入数据
              </button>
              <button className="btn btn--ghost" onClick={onSeed} disabled={busy}>
                <IconRefresh size={16} /> 载入示例数据
              </button>
            </div>
          </div>
        </div>
      ) : visible.length === 0 ? (
        <div className="card mt-16">
          <div className="empty">
            <IconSearch size={30} style={{ color: 'var(--text-mute)' }} />
            <div className="empty-title">没有符合条件的岗位</div>
            <div className="empty-desc">换个关键词或去掉几个筛选条件试试。</div>
            <button className="btn btn--ghost mt-16" onClick={clearAll}>
              清除全部筛选
            </button>
          </div>
        </div>
      ) : (
        <>
          <div className="jh-tablewrap jh-tablewrap--main">
            <table className="jh-table">
              <caption className="sr-only">
                岗位信息汇总列表，第 {currentPage} / {totalPages} 页，共 {visible.length} 条
              </caption>
              <thead>
                <tr>
                  <th className="jh-sticky-1 jh-col-idx">序号</th>
                  <th className="jh-sticky-2">
                    <button type="button" className="jh-sort" onClick={() => toggleSort('company')}>
                      企业名称
                      {sort.key === 'company' && <span className="jh-sort-dir">{sort.dir === 'desc' ? '↓' : '↑'}</span>}
                    </button>
                  </th>
                  <th>雇主类型</th>
                  <th>行业</th>
                  <th>毕业届别</th>
                  <th>工作地点</th>
                  <th className="jh-col-wrap">专业要求</th>
                  <th className="jh-col-wrap">招聘岗位</th>
                  <th>
                    <button type="button" className="jh-sort" onClick={() => toggleSort('progress')}>
                      投递进度
                      {sort.key === 'progress' && <span className="jh-sort-dir">{sort.dir === 'desc' ? '↓' : '↑'}</span>}
                    </button>
                  </th>
                  <th>
                    <button type="button" className="jh-sort" onClick={() => toggleSort('posted_at')}>
                      更新时间
                      {sort.key === 'posted_at' && <span className="jh-sort-dir">{sort.dir === 'desc' ? '↓' : '↑'}</span>}
                    </button>
                  </th>
                  <th title="投递链接">投递链接</th>
                  <th className="jh-sticky-r">
                    <span className="sr-only">操作</span>
                  </th>
                </tr>
              </thead>
              <tbody>
                {pageItems.map((r, i) => {
                  const old = daysOld(r.posted_at)
                  /* 序号是**全局位次**（跨页连续），不是「本页第几条」：
                     在手机上翻到第 2 页时，「第 34 条」比「第 4 条」更接近用户对
                     「我这一屏是整份表的哪一段」的判断。 */
                  const seq = (currentPage - 1) * JOB_PAGE_SIZE + i + 1
                  return (
                    <tr key={r.job_id}>
                      <td className="jh-sticky-1 jh-col-idx">{seq}</td>
                      {/* 企业名称：备注已不再在列表展示，这里只呈现名称本身，
                          不再做成可点击展开的按钮（避免给出「点了会展开」的假承诺）。 */}
                      <td className="jh-sticky-2 jh-company-cell">{r.company}</td>
                      <td>{r.nature ? <span className="jh-tag">{r.nature}</span> : <span className="mute-2">—</span>}</td>
                      <td>{r.industry ? <span className="jh-tag">{r.industry}</span> : <span className="mute-2">—</span>}</td>
                      <td className="jh-ellip" title={r.audience}>{r.audience || '—'}</td>
                      <td className="jh-ellip" title={(r.locations || []).join('、')}>{(r.locations || []).join('、') || '—'}</td>
                      {/* 专业要求 / 招聘岗位：内容长度差异极大，**折行完整显示**
                          （配 .jh-col-wrap 的 white-space: normal），不做省略号截断 */}
                      <td className="jh-col-wrap">{(r.majors || []).join('、') || '—'}</td>
                      <td className="jh-col-wrap">{(r.positions || []).join('、') || '—'}</td>
                      <td>{progressCell(r)}</td>
                      <td className="jh-col-date">
                        {r.posted_at ? (
                          <>
                            <div className={`jh-date ${old > 21 ? 'is-old' : ''}`}>{r.posted_at}</div>
                            <div className="jh-date-sub">{relativeDay(r.posted_at)}</div>
                          </>
                        ) : (
                          <span className="mute-2">—</span>
                        )}
                      </td>
                      <td>{linkBtn(r.apply_url, '投递', IconArrowRight)}</td>
                      <td className="jh-sticky-r">
                        <div className="jh-row-actions">
                          <button
                            type="button"
                            className="jh-icon-btn"
                            aria-haspopup="menu"
                            aria-expanded={menuFor === r.job_id}
                            aria-label={`${r.company} 的更多操作`}
                            onClick={(e) => {
                              e.stopPropagation()
                              setMenuFor(menuFor === r.job_id ? '' : r.job_id)
                            }}
                          >
                            <IconDots size={16} />
                          </button>
                          {menuFor === r.job_id && (
                            <div className="jh-menu" role="menu" onClick={(e) => e.stopPropagation()}>
                              <div className="jh-menu-company">{r.company}</div>
                              <button
                                type="button"
                                role="menuitem"
                                className="jh-menu-item jh-menu-item--danger"
                                onClick={() => {
                                  setMenuFor('')
                                  confirmDelete(r)
                                }}
                              >
                                <IconTrash size={15} /> 删除岗位
                              </button>
                              <p className="jh-menu-hint">会连带清除所有人在该岗位上的投递进度</p>
                            </div>
                          )}
                        </div>
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>

          {/* 窄屏卡片形态：13 列表格在手机上只能横向滑，读起来很累。
              字段与桌面表格保持一致（含新增的「行业」，去掉公告与备注两列）。 */}
          <ul className="jh-cards">
            {pageItems.map((r, i) => (
              <li className="jh-card" key={r.job_id}>
                <div className="jh-card-head">
                  <span className="jh-card-idx mono">{(currentPage - 1) * JOB_PAGE_SIZE + i + 1}</span>
                  <span className="jh-card-company">{r.company}</span>
                  {r.nature && <span className="jh-tag">{r.nature}</span>}
                  {r.industry && <span className="jh-tag">{r.industry}</span>}
                </div>
                <div className="jh-card-meta">
                  {r.audience && <span title="毕业届别">{r.audience}</span>}
                  {r.locations?.length ? <span title="工作地点">{r.locations.join('、')}</span> : null}
                  {r.majors?.length ? <span title="专业要求">{r.majors.join('、')}</span> : null}
                  {r.positions?.length ? <span title="招聘岗位">{r.positions.join('、')}</span> : null}
                  {r.posted_at && (
                    <span title="岗位信息更新时间">
                      {r.posted_at} · {relativeDay(r.posted_at)}
                    </span>
                  )}
                </div>
                <div className="jh-card-foot">
                  {progressCell(r)}
                  <div className="row gap-8" style={{ marginLeft: 'auto' }}>
                    {r.apply_url && (
                      <a className="btn btn--ghost btn--sm" href={fullUrl(r.apply_url)} target="_blank" rel="noopener noreferrer">
                        <IconArrowRight size={14} /> 投递
                      </a>
                    )}
                    <button
                      type="button"
                      className="btn btn--danger btn--sm"
                      onClick={() => confirmDelete(r)}
                      aria-label={`删除 ${r.company}`}
                    >
                      <IconTrash size={14} />
                    </button>
                  </div>
                </div>
              </li>
            ))}
          </ul>

          {/* 分页：每页 30 条，翻页按钮就在列表下方 */}
          <JobPager
            page={currentPage}
            total={visible.length}
            onChange={setPage}
            label="岗位信息汇总列表分页"
          />

          <p className="jh-foot">
            共 {records.length} 条岗位 · 当前筛选出 {visible.length} 条 · 已投递{' '}
            {records.filter((r) => isApplied(r.progress)).length} 条
            <span className="mute-2">　岗位信息全员共享、登录后即可维护；投递进度各自独立保存。</span>
          </p>
        </>
      )}

      {/* ---------------- 数据区（所有登录用户） ---------------- */}
      <section className="card jh-data-card">
        <div className="card-head">
          <div className="card-title">
            <IconBuilding size={16} /> 岗位库维护
          </div>
          <span className="mute-2" style={{ fontSize: 12 }}>
            改动立即写入共享岗位库，对所有人生效
          </span>
        </div>
        <div className="jh-data-actions">
          <button className="btn btn--primary btn--sm" onClick={onImport} disabled={busy}>
            <IconUpload size={15} /> 导入数据
          </button>
          <button className="btn btn--ghost btn--sm" onClick={onSeed} disabled={busy}>
            <IconRefresh size={15} /> 载入示例数据
          </button>
          <button className="btn btn--danger btn--sm" onClick={onClearAll} disabled={busy || !records.length}>
            <IconTrash size={15} /> 清空全部岗位
          </button>
        </div>
        <p className="jh-hint">
          导入支持 <strong>.xlsx</strong> / <strong>.csv</strong> / 从 Excel 直接粘贴，自动识别表头（含「所属行业 / 行业类别」等同义词）；
          只导入 10 个岗位信息字段，「投递进度」与「更新时间」不参与导入
          （进度一律记为「未投递」、更新时间记为导入当天），
          避免从别处拿到的汇总表污染你自己的投递记录。清空与删除会连带清除所有人的相关进度。
          需要带走数据时，用「投递统计」里的<strong>导出统计 Excel</strong>——它导出的是
          <strong>你自己已投递的那部分</strong>，而不是整份共享岗位库。
        </p>
      </section>

      {/* 全是「未投递」时的轻提示：说清这个页面还能做什么，避免以为功能没生效 */}
      {!emptyLibrary && !records.some((r) => isApplied(r.progress)) && (
        <p className="jh-hint jh-hint--info">
          还没有任何已投递记录。点表格里的「投递进度」徽章（手机端点卡片上的徽章）把状态改成
          「简历初筛 / 已测评 / …」，「投递统计」里就会出现漏斗与各环节的分布——
          统计只算「{JOB_STATUS_DEFAULT}」以外的状态。
        </p>
      )}
    </>
  )
}

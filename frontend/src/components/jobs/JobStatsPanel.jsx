import { useEffect, useMemo, useState } from 'react'
import { isApplied, jobStageOf, jobTypeOf } from '../../constants/jobStatus'
import { daysOld, relativeDay, todayStr } from '../../lib/jobFormat'
import { downloadBlob, writeXlsx } from '../../lib/xlsx'
import { notify } from '../../api/client'
import JobStatusBadge from './JobStatusBadge'
import JobPager, { JOB_PAGE_SIZE } from './JobPager'
import { IconCheck, IconClock, IconDownload, IconFunnel, IconInbox, IconSearch, IconTarget, IconWarn } from '../icons'

/**
 * 投递统计：只统计「投递进度不为未投递」的岗位。
 *
 * 聚合值（漏斗 / 各环节淘汰 / 两个分布 / 5 个 KPI）来自服务端 `/api/jobs/stats`：
 * 「漏斗怎么按阶段递进推算」「已挂怎么归因到环节」这类口径只能有一份实现，
 * 放在前端就等于每个客户端各算一遍。**明细表**的搜索与排序仍在本页本地做：
 * 数据量很小，交互要即时，为一次筛选多跑一趟服务端没有意义。
 *
 * 一个刻意的排序规则：**「已挂」的记录永远沉底**——无论按哪一列排，
 * 淘汰记录都不会和仍在推进的机会混在一起（统一规则，保留）。
 */
export default function JobStatsPanel({ stats, records, busy, onPickStatus, onGoList }) {
  const [query, setQuery] = useState('')
  const [sort, setSort] = useState({ key: 'progress_at', dir: 'desc' })
  const [page, setPage] = useState(1)

  const applied = useMemo(() => records.filter((r) => isApplied(r.progress)), [records])

  const rows = useMemo(() => {
    const q = query.trim().toLowerCase()
    const list = applied.filter((r) => {
      if (!q) return true
      return [r.company, r.nature, r.progress, ...(r.positions || [])]
        .filter(Boolean)
        .join(' ')
        .toLowerCase()
        .includes(q)
    })
    const dir = sort.dir === 'asc' ? 1 : -1
    return list.sort((a, b) => {
      /* 已挂固定沉底：这一层比较与排序键无关，先按「是否淘汰」分组 */
      const af = jobTypeOf(a.progress) === 'fail' ? 1 : 0
      const bf = jobTypeOf(b.progress) === 'fail' ? 1 : 0
      if (af !== bf) return af - bf
      let va
      let vb
      if (sort.key === 'progress') {
        va = jobStageOf(a.progress) * 10 + (jobTypeOf(a.progress) === 'fail' ? -1 : 0)
        vb = jobStageOf(b.progress) * 10 + (jobTypeOf(b.progress) === 'fail' ? -1 : 0)
        return (va - vb) * dir
      }
      va = sort.key === 'company' ? a.company || '' : a.progress_at || ''
      vb = sort.key === 'company' ? b.company || '' : b.progress_at || ''
      return String(va).localeCompare(String(vb), 'zh-Hans-CN') * dir
    })
  }, [applied, query, sort])

  const toggleSort = (key) =>
    setSort((prev) => ({ key, dir: prev.key === key && prev.dir === 'desc' ? 'asc' : 'desc' }))

  /* ---------- 分页（每页 30 条，与汇总列表同一套规则） ----------
     搜索 / 排序一变回到第 1 页；数据变少时把页码夹回最后一页 */
  const totalPages = Math.max(1, Math.ceil(rows.length / JOB_PAGE_SIZE))
  useEffect(() => {
    setPage(1)
  }, [query, sort])
  useEffect(() => {
    setPage((p) => Math.min(p, totalPages))
  }, [totalPages])
  const currentPage = Math.min(page, totalPages)
  const pageRows = useMemo(
    () => rows.slice((currentPage - 1) * JOB_PAGE_SIZE, currentPage * JOB_PAGE_SIZE),
    [rows, currentPage]
  )

  /* 导出统计：**当前筛选 / 排序下的全部已投递记录**（不是当前页），6 列（统一口径） */
  const exportStats = async () => {
    if (!rows.length) {
      notify('没有可导出的已投递记录', 'warn')
      return
    }
    try {
      const table = [['序号', '企业名称', '更新时间', '雇主类型', '投递岗位', '投递进度']]
      rows.forEach((r, i) => {
        table.push([
          i + 1,
          r.company,
          r.progress_at ? { date: r.progress_at } : '',
          r.nature,
          (r.positions || []).join('、'),
          r.progress,
        ])
      })
      const blob = await writeXlsx([{ name: '投递统计', rows: table }], {
        colWidths: [6, 28, 14, 14, 30, 16],
      })
      downloadBlob(`秋招投递统计-${todayStr()}.xlsx`, blob)
      notify(`已导出投递统计（${rows.length} 条）`, 'success')
    } catch (err) {
      notify(`导出失败：${err?.message || err}`, 'error')
    }
  }

  const s = stats
  const kpis = s
    ? [
        { label: '岗位总数', value: s.total, unit: '条', note: `已投递 ${s.applied} 条 · 投递率 ${s.applied_rate}%`, tone: 'blue', Icon: IconInbox },
        { label: '已投递', value: s.applied, unit: '条', note: `占全部岗位 ${s.applied_rate}%`, tone: 'violet', Icon: IconTarget },
        { label: '进行中', value: s.active, unit: '条', note: '初筛 / 测评 / 面试流程中', tone: 'teal', Icon: IconClock },
        { label: '已挂', value: s.fail, unit: '条', note: `占已投递 ${s.rejection_rate}%`, tone: 'danger', Icon: IconWarn },
        { label: 'Offer / OC', value: s.passed, unit: '条', note: '终态通过', tone: 'ok', Icon: IconCheck },
      ]
    : []

  if (!applied.length) {
    return (
      <div className="card mt-16">
        <div className="empty">
          <IconTarget size={30} style={{ color: 'var(--text-mute)' }} />
          <div className="empty-title">还没有已投递的岗位</div>
          <div className="empty-desc">
            在「汇总列表」里把某条记录的投递进度从「未投递」改成其它值，它就会出现在这里，
            并计入漏斗、各环节淘汰与两个分布。
          </div>
          <button className="btn btn--primary mt-16" onClick={onGoList}>
            去汇总列表
          </button>
        </div>
      </div>
    )
  }

  return (
    <>
      {/* ---------------- KPI ---------------- */}
      <div className="jh-kpis">
        {kpis.map((k) => (
          <div className={`jh-kpi jh-kpi--${k.tone}`} key={k.label}>
            <div className="jh-kpi-label">
              <k.Icon size={15} />
              <span>{k.label}</span>
            </div>
            <div className="jh-kpi-value mono">
              {k.value}
              <small>{k.unit}</small>
            </div>
            <div className="jh-kpi-note">{k.note}</div>
          </div>
        ))}
      </div>

      <div className="jh-panels">
        {/* 投递漏斗 */}
        <section className="card jh-panel">
          <div className="card-head">
            <div className="card-title">
              <IconFunnel size={16} /> 投递漏斗
            </div>
            <span className="mute-2" style={{ fontSize: 12 }} title="按阶段递进推算：未记录中间环节的岗位按「已通过」计">
              递进推算
            </span>
          </div>
          <div className="jh-funnel">
            {s?.funnel?.map((step, i) => (
              <div className="jh-fn-row" key={step.name}>
                <div className="jh-fn-name">{step.name}</div>
                <div className="jh-fn-track">
                  <div className="jh-fn-bar" style={{ width: `${step.percent}%` }} />
                </div>
                <div className="jh-fn-val">
                  <b className="mono">{step.value}</b> 条
                  <span className="jh-rate">{i === 0 ? '—' : `${step.rate}%`}</span>
                </div>
              </div>
            ))}
          </div>
        </section>

        {/* 各环节淘汰 */}
        <section className="card jh-panel">
          <div className="card-head">
            <div className="card-title">
              <IconWarn size={16} /> 各环节淘汰
            </div>
            <span className="mute-2" style={{ fontSize: 12 }}>{s?.reject_total || 0} 次淘汰</span>
          </div>
          {s?.reject_total ? (
            <div className="jh-bars">
              {s.rejected.map((row) => (
                <div className="jh-bar-row" key={row.name}>
                  <div className={`jh-bar-name ${row.value ? 'is-strong' : ''}`}>{row.name}</div>
                  <div className="jh-bar-track">
                    <div className="jh-bar-fill jh-bar-fill--fail" style={{ width: `${row.percent}%` }} />
                  </div>
                  <div className="jh-bar-val">
                    <b className="mono">{row.value}</b> 条
                  </div>
                </div>
              ))}
            </div>
          ) : (
            <p className="jh-panel-empty">太好了，目前没有被淘汰的岗位。</p>
          )}
        </section>

        {/* 投递进度分布 */}
        <section className="card jh-panel">
          <div className="card-head">
            <div className="card-title">
              <IconTarget size={16} /> 投递进度分布
            </div>
            <span className="mute-2" style={{ fontSize: 12 }}>{s?.active || 0} 条进行中</span>
          </div>
          <div className="jh-bars">
            {s?.status_dist?.map((row) => (
              <div className="jh-bar-row" key={row.name}>
                <div className="jh-bar-name" title={row.name}>{row.name}</div>
                <div className="jh-bar-track">
                  <div className={`jh-bar-fill jh-bar-fill--${row.type}`} style={{ width: `${row.percent}%` }} />
                </div>
                <div className="jh-bar-val">
                  <b className="mono">{row.value}</b> 条
                </div>
              </div>
            ))}
          </div>
        </section>

        {/* 雇主类型分布 */}
        <section className="card jh-panel">
          <div className="card-head">
            <div className="card-title">
              <IconInbox size={16} /> 雇主类型分布
            </div>
            <span className="mute-2" style={{ fontSize: 12 }}>{s?.nature_kinds || 0} 类</span>
          </div>
          <div className="jh-bars">
            {s?.nature_dist?.map((row, i) => (
              <div className="jh-bar-row" key={row.name}>
                <div className="jh-bar-name" title={row.name}>{row.name}</div>
                <div className="jh-bar-track">
                  <div className={`jh-bar-fill jh-bar-fill--p${i % 6}`} style={{ width: `${row.percent}%` }} />
                </div>
                <div className="jh-bar-val">
                  <b className="mono">{row.value}</b> 条
                </div>
              </div>
            ))}
          </div>
        </section>
      </div>

      {/* ---------------- 已投递明细 ---------------- */}
      <section className="card jh-panel jh-panel--flush">
        <div className="card-head jh-detail-head">
          <div className="card-title">
            <IconSearch size={16} /> 已投递明细
          </div>
          <span className="mute-2" style={{ fontSize: 12 }}>
            已挂记录固定沉底 · 点「投递进度」可直接修改
          </span>
        </div>

        {/* 搜索框与导出按钮同排：搜索**左对齐**，导出紧贴它右侧。
            此前两者被推到卡片右端、还夹着一行说明文字，眼睛要在同一行来回找。 */}
        <div className="jh-detail-tools">
          <input
            className="input jh-detail-search"
            type="search"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="搜索已投递企业 / 岗位"
            aria-label="搜索已投递岗位"
          />
          <button className="btn btn--ghost btn--sm" onClick={exportStats} disabled={busy || !rows.length}>
            <IconDownload size={15} /> 导出统计 Excel
          </button>
        </div>

        {rows.length === 0 ? (
          <p className="jh-panel-empty">没有匹配的已投递岗位，换个关键词试试。</p>
        ) : (
          <div className="jh-tablewrap jh-tablewrap--flush">
            <table className="jh-table jh-table--stats">
              <caption className="sr-only">
                已投递明细，第 {currentPage} / {totalPages} 页，共 {rows.length} 条
              </caption>
              <thead>
                <tr>
                  <th className="jh-col-idx">序号</th>
                  <th>
                    <button type="button" className="jh-sort" onClick={() => toggleSort('company')}>
                      企业名称
                      {sort.key === 'company' && <span className="jh-sort-dir">{sort.dir === 'desc' ? '↓' : '↑'}</span>}
                    </button>
                  </th>
                  <th>
                    <button type="button" className="jh-sort" onClick={() => toggleSort('progress_at')} title="随投递进度修改自动更新">
                      进度更新时间
                      {sort.key === 'progress_at' && <span className="jh-sort-dir">{sort.dir === 'desc' ? '↓' : '↑'}</span>}
                    </button>
                  </th>
                  <th>雇主类型</th>
                  <th>投递岗位</th>
                  <th>
                    <button type="button" className="jh-sort" onClick={() => toggleSort('progress')}>
                      投递进度
                      {sort.key === 'progress' && <span className="jh-sort-dir">{sort.dir === 'desc' ? '↓' : '↑'}</span>}
                    </button>
                  </th>
                </tr>
              </thead>
              <tbody>
                {pageRows.map((r, i) => {
                  const pOld = daysOld(r.progress_at)
                  /* 序号跨页连续（与汇总列表同一口径） */
                  const seq = (currentPage - 1) * JOB_PAGE_SIZE + i + 1
                  return (
                    <tr key={r.job_id} className={jobTypeOf(r.progress) === 'fail' ? 'is-failed' : ''}>
                      <td className="jh-col-idx">{seq}</td>
                      <td className="jh-company-cell">{r.company}</td>
                      <td className="jh-col-date">
                        {r.progress_at ? (
                          <>
                            <div className={`jh-date ${pOld > 21 ? 'is-old' : ''}`}>{r.progress_at}</div>
                            <div className="jh-date-sub">{relativeDay(r.progress_at)}</div>
                          </>
                        ) : (
                          <span className="mute-2">—</span>
                        )}
                      </td>
                      <td>{r.nature ? <span className="jh-tag">{r.nature}</span> : <span className="mute-2">—</span>}</td>
                      <td className="jh-ellip" title={(r.positions || []).join('、')}>
                        {(r.positions || []).join('、') || '—'}
                      </td>
                      <td>
                        <JobStatusBadge status={r.progress} interactive size="sm" onClick={() => onPickStatus(r)} />
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        )}

        {rows.length > 0 && (
          <div className="jh-detail-pager">
            <JobPager
              page={currentPage}
              total={rows.length}
              onChange={setPage}
              label="已投递明细分页"
            />
          </div>
        )}

        <p className="jh-hint">
          「进度更新时间」是<strong>你的进度</strong>最后一次变动的时间，与列表页的「更新时间」
          （岗位信息进库时间，只读）不是一回事。漏斗按阶段递进推算，
          若你跳过了某个环节（如免笔试），漏斗会把它算作已通过——明细表才是精确事实。
          导出的统计 Excel 是<strong>当前筛选 / 排序下的全部已投递记录</strong>，不受分页影响。
        </p>
      </section>
    </>
  )
}

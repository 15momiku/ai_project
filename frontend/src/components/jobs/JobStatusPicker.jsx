import { useState } from 'react'
import { JOB_STATUS_DEFAULT, JOB_STATUS_GROUPS, jobTypeOf } from '../../constants/jobStatus'
import JobDialog from './JobDialog'
import JobStatusBadge from './JobStatusBadge'
import { IconCheck } from '../icons'

/**
 * 投递进度选择器：按「未投递 / 筛选中 / 面试中 / 已挂 / 终态」分组列出全部状态。
 *
 * 分组不是为了好看：15 个状态平铺成一个列表时，「已初面」和「初面挂」会挨在一起，
 * 误点一次就把「进了初面」记成「挂在初面」。按阶段语义分组后，
 * 「往前进」和「已淘汰」在视觉上分开了。
 *
 * 归属由服务端按令牌判定，因此这里只有一个动作：选中某个状态并提交。
 * 「未投递」是合法的选择（表示清除记录），不是「取消」。
 */
export default function JobStatusPicker({ record, saving = false, onPick, onClose }) {
  const [pending, setPending] = useState(record?.progress || JOB_STATUS_DEFAULT)

  return (
    <JobDialog
      title="修改投递进度"
      subtitle={record?.company ? `${record.company}${record.positions?.length ? ` · ${record.positions.join('、')}` : ''}` : ''}
      onClose={onClose}
      footer={
        <div className="row row--between gap-12">
          <span className="mute-2" style={{ fontSize: 12 }}>
            进度只属于你自己，其他人看不到
          </span>
          <div className="row gap-8">
            <button type="button" className="btn btn--ghost btn--sm" onClick={onClose}>
              取消
            </button>
            <button
              type="button"
              className="btn btn--primary btn--sm"
              disabled={saving || pending === record?.progress}
              onClick={() => onPick(pending)}
            >
              {saving ? <span className="spinner" /> : null}
              {saving ? '保存中…' : '保存'}
            </button>
          </div>
        </div>
      }
    >
      <div className="jh-picker">
        {JOB_STATUS_GROUPS.map((group) => (
          <div className="jh-picker-group" key={group.label}>
            <div className="jh-picker-label">{group.label}</div>
            <div className="jh-picker-items">
              {group.items.map((status) => {
                const on = pending === status
                return (
                  <button
                    key={status}
                    type="button"
                    className={`jh-picker-item jh-picker-item--${jobTypeOf(status)} ${on ? 'on' : ''}`}
                    aria-pressed={on}
                    onClick={() => setPending(status)}
                  >
                    <JobStatusBadge status={status} size="sm" />
                    {on && <IconCheck size={15} />}
                  </button>
                )
              })}
            </div>
          </div>
        ))}
      </div>
      {pending === JOB_STATUS_DEFAULT && record?.progress !== JOB_STATUS_DEFAULT && (
        <p className="jh-hint jh-hint--warn">
          选「未投递」会<strong>清除</strong>这条岗位的投递记录（进度更新时间也会一并消失），
          适合记错了要撤销的情况。
        </p>
      )}
    </JobDialog>
  )
}

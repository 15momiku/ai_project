import { useEffect, useRef } from 'react'
import { IconX } from '../icons'

/**
 * 岗位信息汇总的通用弹层（进度选择器 / 导入向导共用）。
 *
 * 为什么单独封一个而不是各写一份：本项目此前每个弹层都自己写一遍遮罩、
 * Esc 关闭与滚动锁，结果「有的能按 Esc、有的不能」这种不一致反复出现。
 * 这里把三件事一次做对：
 *   · **Esc 关闭**：键盘用户不必去找那个「取消」按钮；
 *   · **锁 body 滚动**：弹层下面的表格不会跟着滑（移动端尤其明显）；
 *   · **焦点管理**：打开时把焦点移进弹层，关闭时还给打开它的元素。
 *
 * 无障碍：`role="dialog"` + `aria-modal="true"` + `aria-labelledby`，
 * 屏幕阅读器会把弹层当成一个独立上下文，而不是页面里突然多出来的一段文字。
 */
export default function JobDialog({
  title,
  subtitle = '',
  onClose,
  children,
  footer = null,
  wide = false,
  labelledBy = 'jh-dialog-title',
}) {
  const boxRef = useRef(null)
  const restoreRef = useRef(null)

  useEffect(() => {
    restoreRef.current = document.activeElement
    const onKey = (e) => {
      if (e.key === 'Escape') {
        e.stopPropagation()
        onClose?.()
      }
    }
    document.addEventListener('keydown', onKey)
    const prev = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    // 焦点进入弹层：优先聚焦第一个可交互元素，没有就聚焦容器本身
    const focusable = boxRef.current?.querySelector(
      'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])'
    )
    ;(focusable || boxRef.current)?.focus?.()
    return () => {
      document.removeEventListener('keydown', onKey)
      document.body.style.overflow = prev
      const back = restoreRef.current
      if (back && typeof back.focus === 'function') back.focus()
    }
  }, [onClose])

  return (
    <div className="jh-mask" onMouseDown={(e) => e.target === e.currentTarget && onClose?.()}>
      <div
        className={`jh-modal ${wide ? 'jh-modal--wide' : ''}`}
        role="dialog"
        aria-modal="true"
        aria-labelledby={labelledBy}
        tabIndex={-1}
        ref={boxRef}
      >
        <div className="jh-modal-head">
          <div className="grow">
            <h2 className="jh-modal-title" id={labelledBy}>
              {title}
            </h2>
            {subtitle ? <p className="jh-modal-sub">{subtitle}</p> : null}
          </div>
          <button type="button" className="jh-icon-btn" onClick={onClose} aria-label="关闭">
            <IconX size={18} />
          </button>
        </div>
        <div className="jh-modal-body">{children}</div>
        {footer ? <div className="jh-modal-foot">{footer}</div> : null}
      </div>
    </div>
  )
}

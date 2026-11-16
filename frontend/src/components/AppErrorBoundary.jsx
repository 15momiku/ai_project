import { Component } from 'react'

/**
 * 全局错误边界（体验增强 §4.5 状态完备性）
 *
 * 为什么必须有：React 18 里渲染期抛出的异常若没有错误边界接住，React 会
 * **卸载整棵组件树** —— 用户看到的就是「界面突然一片空白、点什么都没反应」，
 * 控制台里只有一行报错，页面上没有任何可操作的出口。
 *
 * 报告页真实踩过一次：7 天计划闭环新增的几个 hooks 被写在「加载中 / 加载失败」
 * 两个提前 return 之后，数据到达后的第二次渲染 hooks 数量变多 → React 抛
 * 「Rendered more hooks than during the previous render」→ 白屏。
 * 根因已修（hooks 全部前置），本组件负责**兜底**：任何渲染异常都退化成一张
 * 可读的卡片（原因 + 重试 + 刷新 + 回首页），而不是白屏。
 *
 * 边界只能接住**渲染期**（含子组件构造函数/render）抛出的异常；
 * 事件回调、Promise、setTimeout 里的异常仍由各自的 try/catch 处理。
 *
 * 放在最外层（包住 BrowserRouter 与 AuthProvider）：这两个自身出错时也能被接住，
 * 因此 fallback 里不依赖路由与登录态，只用 window.location 做整页跳转。
 */
export default class AppErrorBoundary extends Component {
  constructor(props) {
    super(props)
    this.state = { error: null, stack: '', copied: false }
  }

  static getDerivedStateFromError(error) {
    return { error }
  }

  componentDidCatch(error, info) {
    // 保留组件栈：用户复制出来就能定位到具体组件
    this.setState({ stack: info?.componentStack || '' })
    console.error('[AppErrorBoundary] 页面渲染异常：', error, info)
  }

  onRetry = () => {
    // 清掉错误状态重试渲染。若是确定性错误会再次落到这里，不会无限循环
    this.setState({ error: null, stack: '', copied: false })
  }

  onReload = () => {
    window.location.reload()
  }

  onHome = () => {
    // 整页跳转而非路由跳转：出错时组件状态可能已经不干净，重新加载最稳
    window.location.assign('/')
  }

  onCopy = async () => {
    const { error, stack } = this.state
    const text = `${error?.name || 'Error'}: ${error?.message || error}\n${stack}`
    try {
      await navigator.clipboard.writeText(text)
      this.setState({ copied: true })
    } catch {
      // 非 HTTPS / 无权限时静默失败：下面还有可折叠的详情可以手动复制
    }
  }

  render() {
    const { error, stack, copied } = this.state
    if (!error) return this.props.children

    const detail = `${error?.name || 'Error'}: ${error?.message || String(error)}`
    return (
      <div className="container">
        <div className="card mt-16">
          <div className="empty">
            <div className="empty-title">页面出错了</div>
            <div className="empty-desc">
              这一页在渲染时遇到了问题，已经停下以免影响其他数据。
              你的面试记录与报告都已保存在服务端，重新进入即可继续。
            </div>
            <div className="row gap-12 mt-16" style={{ justifyContent: 'center', flexWrap: 'wrap' }}>
              <button className="btn btn--primary" onClick={this.onRetry}>
                重试
              </button>
              <button className="btn btn--ghost" onClick={this.onReload}>
                刷新页面
              </button>
              <button className="btn btn--ghost" onClick={this.onHome}>
                回到首页
              </button>
            </div>
            {typeof navigator !== 'undefined' && navigator.clipboard && (
              <button className="link-btn mt-12" onClick={this.onCopy} type="button">
                {copied ? '错误信息已复制' : '复制错误信息（便于排查）'}
              </button>
            )}
          </div>
          <details className="mt-12">
            <summary className="mute-2" style={{ cursor: 'pointer' }}>
              技术详情
            </summary>
            <pre
              className="mono"
              style={{
                whiteSpace: 'pre-wrap',
                wordBreak: 'break-word',
                fontSize: 12,
                lineHeight: 1.6,
                marginTop: 8,
              }}
            >
              {detail}
              {stack}
            </pre>
          </details>
        </div>
      </div>
    )
  }
}

// ============================================================
// KB §18 WebSocket 实时网关客户端（原生 WebSocket，REDUN-01 兜底）
// 频道 channel: task:{id} / video:{genId} / notifications / credits
// 事件 event: connected / subscribed / pong / status_update / progress / log / completed / failed / balance_update
// 心跳：客户端每 30s 发送 ping，60s 内未收到 pong 判定断线 → 重连并重新订阅
// 降级：组件在 ws.connected === false 时回退到轮询（REDUN-01），保证 STR-01 不丢状态
// ============================================================

const WS_PATH = '/ws'

// WS 网关主机候选（personal_local_only 单机型）：
//   1) 直连后端 WS 网关 127.0.0.1:7788（KB §18 /ws 由后端 FastAPI 直出，最稳）
//   2) 回退前端同主机反代 location.host（若将来 8099 补齐 /ws 代理）
// 默认直连后端，避免无谓的 8099 握手失败噪声；若 7788 不可用自动回退反代。
const _WS_HOSTS = ['127.0.0.1:7788', location.host]
let _wsHostIdx = 0
function wsHost() {
  return _WS_HOSTS[Math.min(_wsHostIdx, _WS_HOSTS.length - 1)]
}

function wsUrl() {
  const proto = location.protocol === 'https:' ? 'wss:' : 'ws:'
  const token = localStorage.getItem('wb_token') || 'local'
  return proto + '//' + wsHost() + WS_PATH + '?token=' + encodeURIComponent(token)
}

class WsClient {
  constructor() {
    this.socket = null
    this.connected = false
    this.handlers = new Map() // channel -> Set(fn)
    this.globalHandlers = new Set() // fn(channel, event, data, msg)
    this.reconnectDelay = 1000
    this.maxReconnect = 15000
    this.pingTimer = null
    this.pongTimer = null
    this.lastPong = 0
    this._closed = false
    this._reconnectTimer = null
  }

  connect() {
    if (this._closed) return
    if (this.socket && (this.socket.readyState === WebSocket.OPEN || this.socket.readyState === WebSocket.CONNECTING)) return
    let sock
    try {
      sock = new WebSocket(wsUrl())
    } catch (e) {
      this._scheduleReconnect()
      return
    }
    this.socket = sock

    sock.onopen = () => {
      this.connected = true
      this.reconnectDelay = 1000
      this._startPing()
      // 断线重连后重新订阅所有频道（CLOSE-03 幂等：subscribe 不会重复叠加 handler）
      // 注意：服务端 handle_ws 只认 payload.channels，顶层 channel 字段会被忽略，
      // 故重订阅必须按服务端协议封装（否则订阅静默失效 → 导演事件黑洞）。
      this.handlers.forEach((_, channel) => this._send({ action: 'subscribe', payload: { channels: [channel] } }))
    }
    sock.onmessage = (ev) => {
      let msg
      try { msg = JSON.parse(ev.data) } catch { return }
      this._dispatch(msg)
    }
    sock.onclose = () => {
      this.connected = false
      this._stopPing()
      this._scheduleReconnect()
    }
    sock.onerror = () => {
      // 当前候选主机握手失败 → 切换到下一个候选（直连 7788 失败则回退前端反代）
      if (!this.connected && _wsHostIdx < _WS_HOSTS.length - 1) {
        _wsHostIdx += 1
      }
    }
  }

  _startPing() {
    this._stopPing()
    // 心跳 ping 30s（KB §18）/ 60s 内无 pong 主动断开（OPS 心跳）
    this.pingTimer = setInterval(() => {
      this._send({ action: 'ping' })
      this.pongTimer = setTimeout(() => {
        if (this.socket && this.socket.readyState === WebSocket.OPEN) {
          try { this.socket.close() } catch { /* noop */ }
        }
      }, 60000)
    }, 30000)
  }

  _stopPing() {
    if (this.pingTimer) clearInterval(this.pingTimer)
    if (this.pongTimer) clearTimeout(this.pongTimer)
    this.pingTimer = null
    this.pongTimer = null
  }

  _send(obj) {
    if (this.socket && this.socket.readyState === WebSocket.OPEN) {
      this.socket.send(JSON.stringify(obj))
      return true
    }
    return false
  }

  _dispatch(msg) {
    const { channel, event, data } = msg || {}
    if (event === 'pong') {
      this.lastPong = Date.now()
      if (this.pongTimer) clearTimeout(this.pongTimer)
      return
    }
    if (channel && this.handlers.has(channel)) {
      this.handlers.get(channel).forEach((fn) => {
        try { fn(event, data || {}, msg) } catch (e) { /* 单 handler 异常不影响其他 */ }
      })
    }
    this.globalHandlers.forEach((fn) => {
      try { fn(channel, event, data || {}, msg) } catch (e) { /* noop */ }
    })
  }

  subscribe(channel, fn) {
    if (!this.handlers.has(channel)) this.handlers.set(channel, new Set())
    this.handlers.get(channel).add(fn)
    this.connect()
    // 服务端协议：action=subscribe 必须带 payload.channels（顶层 channel 字段无效）
    this._send({ action: 'subscribe', payload: { channels: [channel] } })
    return () => this.unsubscribe(channel, fn)
  }

  unsubscribe(channel, fn) {
    if (!this.handlers.has(channel)) return
    const set = this.handlers.get(channel)
    if (fn) set.delete(fn)
    else set.clear()
    if (!set.size) this._send({ action: 'unsubscribe', payload: { channels: [channel] } })
  }

  onGlobal(fn) {
    this.globalHandlers.add(fn)
    return () => this.globalHandlers.delete(fn)
  }

  _scheduleReconnect() {
    if (this._closed || this._reconnectTimer) return
    this._reconnectTimer = setTimeout(() => {
      this._reconnectTimer = null
      this.connect()
    }, this.reconnectDelay)
    this.reconnectDelay = Math.min(Math.round(this.reconnectDelay * 1.7), this.maxReconnect)
  }

  close() {
    this._closed = true
    this._stopPing()
    if (this._reconnectTimer) { clearTimeout(this._reconnectTimer); this._reconnectTimer = null }
    if (this.socket) { try { this.socket.close() } catch { /* noop */ } }
  }
}

export const ws = new WsClient()

export function connectWs() { ws.connect() }
export function subscribe(channel, fn) { return ws.subscribe(channel, fn) }
export function unsubscribe(channel, fn) { ws.unsubscribe(channel, fn) }
export function onWsGlobal(fn) { return ws.onGlobal(fn) }
export function wsConnected() { return ws.connected }

// 便捷订阅单个任务频道（task:{id}），自动映射状态事件到回调
// onEvent(event, data) 收到的 event ∈ {status_update, progress, log, completed, failed, subscribed}
export function subscribeTask(taskId, onEvent) {
  if (!taskId) return () => {}
  return ws.subscribe('task:' + taskId, onEvent)
}

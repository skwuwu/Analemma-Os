import { fetchAuthSession } from '@aws-amplify/auth';
import type { NotificationItem } from './types';

interface WebSocketManagerConfig {
  url: string;
  maxReconnectAttempts?: number;
  onMessage: (notification: NotificationItem) => void;
  onComponentStream?: (data: any) => void;
  onConnected?: () => void;
  onDisconnected?: () => void;
}

export class WebSocketManager {
  private ws: WebSocket | null = null;
  private config: WebSocketManagerConfig;
  private reconnectAttempts = 0;
  private reconnectTimeoutId: NodeJS.Timeout | null = null;
  private isReconnecting = false;
  private maxReconnectAttempts: number;

  constructor(config: WebSocketManagerConfig) {
    this.config = config;
    this.maxReconnectAttempts = config.maxReconnectAttempts || 10;
  }

  // Update callbacks dynamically (for React state/props changes)
  updateCallbacks(callbacks: Partial<Pick<WebSocketManagerConfig, 'onMessage' | 'onComponentStream' | 'onConnected' | 'onDisconnected'>>) {
    Object.assign(this.config, callbacks);
  }

  async connect(): Promise<void> {
    if (this.ws && (this.ws.readyState === WebSocket.OPEN || this.ws.readyState === WebSocket.CONNECTING)) {
      return;
    }

    if (this.ws) {
      try { this.ws.close(); } catch (e) { /* ignore */ }
      this.ws = null;
    }

    if (!this.config.url) {
      console.error('❌ WebSocket URL not configured');
      return;
    }

    let url = this.config.url;
    let token: string | null = null;

    try {
      const session = await fetchAuthSession({ forceRefresh: this.reconnectAttempts > 0 });
      token = session.tokens?.idToken?.toString() || null;
    } catch (e) {
      console.warn('Failed to get auth token for WS connection', e);
    }

    if (token) {
      const sep = url.includes('?') ? '&' : '?';
      url = `${url}${sep}token=${encodeURIComponent(token)}`;
    }

    this.ws = new WebSocket(url);

    this.ws.onopen = () => {
      console.log('✅ Notifications WS connected');
      this.reconnectAttempts = 0;
      this.isReconnecting = false;
      this.cancelReconnect();
      this.config.onConnected?.();
    };

    this.ws.onmessage = (ev) => {
      try {
        const data = JSON.parse(ev.data);
        if (!data || typeof data !== 'object') return;

        if (data.type === 'workflow_status') {
          const payload = data.payload || {};
          const action = payload.action || 'workflow_status';
          let status = payload.status;

          if (action === 'hitp_pause' && !status) status = 'PAUSED_FOR_HITP';
          if (action === 'execution_progress' && !status) status = 'RUNNING';

          const notification: NotificationItem = {
            id: payload.conversation_id || payload.execution_id || crypto.randomUUID(),
            type: data.type,
            action: action,
            conversation_id: payload.conversation_id,
            execution_id: payload.execution_id,
            workflowId: payload.workflowId,
            workflow_name: payload.workflow_config?.name,
            message: payload.message || 'Workflow status update',
            segment_to_run: payload.segment_to_run,
            total_segments: payload.total_segments,
            current_segment: payload.current_segment,
            current_state: payload.current_state,
            pre_hitp_output: payload.current_state || payload.pre_hitp_output || null,
            receivedAt: Date.now(),
            read: false,
            payload: payload,
            raw: data,
            status: status,
            workflow_config: payload.workflow_config,
            step_function_state: payload.step_function_state,
            estimated_completion_time: payload.estimated_completion_time,
            estimated_remaining_seconds: payload.estimated_remaining_seconds,
            current_step_label: payload.current_step_label,
            average_segment_duration: payload.average_segment_duration,
            state_durations: payload.state_durations,
            start_time: payload.start_time,
            last_update_time: payload.last_update_time,
            sequence_number: payload.sequence_number,
            server_timestamp: payload.server_timestamp,
            segment_sequence: payload.segment_sequence,
          };

          this.config.onMessage(notification);
        } else if (data.type === 'workflow_component_stream') {
          if (this.config.onComponentStream) {
            const componentData = typeof data.payload === 'string' 
              ? JSON.parse(data.payload) 
              : data.payload;
            this.config.onComponentStream(componentData);
          }
        }
      } catch (e) {
        console.error('Failed to parse WS message:', e);
      }
    };

    this.ws.onclose = (ev) => {
      console.log(`Notifications WS closed (code: ${ev.code})`);
      this.ws = null;
      this.config.onDisconnected?.();
      
      if (ev.code !== 1000) {
        this.scheduleReconnect();
      }
    };

    this.ws.onerror = (e) => {
      console.error('Notifications WS error', e);
    };
  }

  disconnect(): void {
    this.ws?.close();
    this.ws = null;
    this.cancelReconnect();
  }

  private scheduleReconnect(): void {
    if (this.reconnectAttempts >= this.maxReconnectAttempts) {
      console.error(`❌ WebSocket 재연결 최대 시도 횟수(${this.maxReconnectAttempts}) 초과`);
      this.isReconnecting = false;
      return;
    }

    if (this.isReconnecting) {
      console.log('🔄 WebSocket 재연결 이미 진행 중...');
      return;
    }

    this.isReconnecting = true;
    this.reconnectAttempts += 1;

    const delay = Math.min(1000 * Math.pow(2, this.reconnectAttempts - 1), 30000);
    console.log(`🔄 WebSocket 재연결 시도 ${this.reconnectAttempts}/${this.maxReconnectAttempts} - ${delay}ms 후`);

    this.reconnectTimeoutId = setTimeout(async () => {
      try {
        await this.connect();
      } catch (error) {
        console.error('❌ WebSocket 재연결 실패:', error);
        this.isReconnecting = false;
        this.scheduleReconnect();
      }
    }, delay);
  }

  private cancelReconnect(): void {
    if (this.reconnectTimeoutId) {
      clearTimeout(this.reconnectTimeoutId);
      this.reconnectTimeoutId = null;
    }
    this.isReconnecting = false;
  }

  isConnected(): boolean {
    return this.ws?.readyState === WebSocket.OPEN;
  }
}

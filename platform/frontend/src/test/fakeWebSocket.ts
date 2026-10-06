// A minimal hand-rolled fake WebSocket global for the `/ws/live` subscription hooks (Story 15.5,
// moved here from useLiveCandle.test.ts in Story 33.4 so useLiveDerivs/useLiveLiquidations share
// it). Their socket lifecycle (subscribe-on-open, resubscribe-on-reconnect,
// unsubscribe-on-teardown) is the thing under test, so -- unlike RankingsPage.test.tsx, which mocks
// useLiveChannel at the module level -- the global itself is faked. Kept deliberately small: just
// enough surface (onopen/onmessage/onclose/onerror/send/close/readyState) for the hooks' own code,
// driven manually from each test via open()/receive()/close().
export class FakeWebSocket {
  static readonly CONNECTING = 0;
  static readonly OPEN = 1;
  static readonly CLOSED = 3;
  static instances: FakeWebSocket[] = [];

  readyState: number = FakeWebSocket.CONNECTING;
  onopen: (() => void) | null = null;
  onmessage: ((event: { data: string }) => void) | null = null;
  onclose: (() => void) | null = null;
  onerror: (() => void) | null = null;
  sent: string[] = [];
  url: string;

  constructor(url: string) {
    this.url = url;
    FakeWebSocket.instances.push(this);
  }

  send(data: string): void {
    this.sent.push(data);
  }

  close(): void {
    this.readyState = FakeWebSocket.CLOSED;
    this.onclose?.();
  }

  // --- test-only driver methods, not part of the real WebSocket API ---
  open(): void {
    this.readyState = FakeWebSocket.OPEN;
    this.onopen?.();
  }

  receive(data: unknown): void {
    this.onmessage?.({ data: JSON.stringify(data) });
  }
}

export function latestSocket(): FakeWebSocket {
  const socket = FakeWebSocket.instances[FakeWebSocket.instances.length - 1];
  if (!socket) throw new Error("no FakeWebSocket instance was created");
  return socket;
}

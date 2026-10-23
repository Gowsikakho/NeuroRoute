import { useEffect, useRef, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { getGetQueueMetricsQueryKey, getGetSimulationQueryKey } from '@workspace/api-client-react';
const REST_FALLBACK_MS = 15_000;
const INITIAL_RETRY_MS = 1_000;
const MAX_RETRY_MS = 30_000;
export function useSimulationStream() {
    const queryClient = useQueryClient();
    const [status, setStatus] = useState('connecting');
    const socketRef = useRef(null);
    useEffect(() => {
        let disposed = false;
        let reconnectTimer;
        let fallbackTimer;
        let retryDelay = INITIAL_RETRY_MS;
        const refreshRestCaches = () => {
            if (disposed || socketRef.current?.readyState === WebSocket.OPEN)
                return;
            void queryClient.refetchQueries({ queryKey: getGetSimulationQueryKey(), exact: true });
            void queryClient.refetchQueries({
                queryKey: getGetQueueMetricsQueryKey({ windowSeconds: 120 }),
                exact: true,
            });
        };
        const startFallback = () => {
            if (fallbackTimer)
                return;
            fallbackTimer = setInterval(refreshRestCaches, REST_FALLBACK_MS);
        };
        const stopFallback = () => {
            if (fallbackTimer)
                clearInterval(fallbackTimer);
            fallbackTimer = undefined;
        };
        const connect = () => {
            if (disposed)
                return;
            if (typeof window.WebSocket === 'undefined') {
                setStatus('reconnecting');
                startFallback();
                reconnectTimer = setTimeout(connect, retryDelay);
                retryDelay = Math.min(retryDelay * 2, MAX_RETRY_MS);
                return;
            }
            const scheme = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
            const socket = new WebSocket(`${scheme}//${window.location.host}/api/ws/simulation`);
            socketRef.current = socket;
            socket.onopen = () => {
                if (disposed || socketRef.current !== socket)
                    return;
                retryDelay = INITIAL_RETRY_MS;
                setStatus('live');
                stopFallback();
                // Refresh the REST snapshots on every successful connection; subsequent
                // stream messages then keep both generated-query caches current.
                void Promise.all([
                    queryClient.refetchQueries({ queryKey: getGetSimulationQueryKey(), exact: true }),
                    queryClient.refetchQueries({
                        queryKey: getGetQueueMetricsQueryKey({ windowSeconds: 120 }),
                        exact: true,
                    }),
                ]);
            };
            socket.onmessage = (event) => {
                if (disposed || socketRef.current !== socket || typeof event.data !== 'string')
                    return;
                try {
                    const payload = JSON.parse(event.data);
                    if (payload.state && typeof payload.state === 'object') {
                        queryClient.setQueryData(getGetSimulationQueryKey(), payload.state);
                    }
                    if (Array.isArray(payload.metrics)) {
                        queryClient.setQueryData(getGetQueueMetricsQueryKey({ windowSeconds: 120 }), payload.metrics);
                    }
                }
                catch {
                    // Ignore malformed frames and keep the connection available for the next snapshot.
                }
            };
            socket.onerror = () => {
                if (!disposed && socketRef.current === socket)
                    setStatus('reconnecting');
                socket.close();
            };
            socket.onclose = () => {
                if (disposed || socketRef.current !== socket)
                    return;
                socketRef.current = null;
                setStatus('reconnecting');
                startFallback();
                reconnectTimer = setTimeout(connect, retryDelay);
                retryDelay = Math.min(retryDelay * 2, MAX_RETRY_MS);
            };
        };
        startFallback();
        connect();
        return () => {
            disposed = true;
            stopFallback();
            if (reconnectTimer)
                clearTimeout(reconnectTimer);
            const socket = socketRef.current;
            socketRef.current = null;
            if (socket && socket.readyState < WebSocket.CLOSING)
                socket.close(1000, 'Dashboard unmounted');
        };
    }, [queryClient]);
    return status;
}

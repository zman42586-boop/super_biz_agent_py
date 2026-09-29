class AIOpsDiagnosisApp {
    constructor() {
        this.button = document.getElementById('startDiagnosisBtn');
        this.output = document.getElementById('diagnosisOutput');
        this.welcome = document.getElementById('welcomeGreeting');
        this.running = false;
        this.button?.addEventListener('click', () => this.start());
    }

    append(text, type = 'status') {
        if (!this.output) return;
        const block = document.createElement('div');
        block.className = `message assistant aiops-message ${type}`;
        const content = document.createElement('div');
        content.className = 'message-content';
        content.innerHTML = window.marked ? marked.parse(text) : text;
        block.appendChild(content);
        this.output.appendChild(block);
        this.output.scrollTop = this.output.scrollHeight;
    }

    renderEvent(event) {
        switch (event.type) {
            case 'plan':
                this.append(`## 执行计划\n${event.message || ''}`);
                break;
            case 'step_complete':
                this.append(`✅ ${event.message || '步骤完成'}`);
                break;
            case 'report':
                this.append(event.report || event.message || '诊断报告已生成', 'report');
                break;
            case 'complete':
                if (event.response) this.append(event.response, 'report');
                this.append('诊断完成。');
                break;
            case 'error':
                this.append(`诊断失败：${event.message || event.data || '未知错误'}`, 'error');
                break;
            default:
                if (event.message) this.append(event.message);
        }
    }

    consumeSseChunk(chunk) {
        for (const line of chunk.split('\n')) {
            if (!line.startsWith('data:')) continue;
            const payload = line.slice(5).trim();
            if (!payload) continue;
            try {
                this.renderEvent(JSON.parse(payload));
            } catch (_) {
                this.append(payload);
            }
        }
    }

    async start() {
        if (this.running) return;
        this.running = true;
        this.button.disabled = true;
        this.button.querySelector('span').textContent = '诊断中…';
        this.welcome?.remove();
        if (this.output) this.output.innerHTML = '';

        try {
            const response = await fetch('/api/aiops', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({session_id: `web-${Date.now()}`}),
            });
            if (!response.ok || !response.body) {
                throw new Error(`HTTP ${response.status}`);
            }

            const reader = response.body.getReader();
            const decoder = new TextDecoder();
            let buffer = '';
            while (true) {
                const {value, done} = await reader.read();
                buffer += decoder.decode(value || new Uint8Array(), {stream: !done});
                const frames = buffer.split('\n\n');
                buffer = frames.pop() || '';
                frames.forEach((frame) => this.consumeSseChunk(frame));
                if (done) break;
            }
            if (buffer) this.consumeSseChunk(buffer);
        } catch (error) {
            this.append(`诊断请求失败：${error.message}`, 'error');
        } finally {
            this.running = false;
            this.button.disabled = false;
            this.button.querySelector('span').textContent = '重新诊断';
        }
    }
}

document.addEventListener('DOMContentLoaded', () => new AIOpsDiagnosisApp());

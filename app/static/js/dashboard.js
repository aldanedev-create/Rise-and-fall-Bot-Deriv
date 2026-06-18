const state = {
    socket: null,
    scanner: null,
    candles: new Map(),
    histories: new Map(),
    signals: [],
    startedAt: null,
    theme: document.documentElement.dataset.theme || "light",
};

const sceneState = {
    ready: false,
    canvas: null,
    renderer: null,
    scene: null,
    camera: null,
    root: null,
    grid: null,
    floor: null,
    bars: [],
    ribbon: null,
    ribbonGeometry: null,
    signalRings: [],
    materials: {},
    pointer: { x: 0, y: 0 },
    pulse: 0,
    resizeObserver: null,
};

document.addEventListener("DOMContentLoaded", () => {
    initTheme();
    bindControls();
    hydrateInitialSignals();
    updateSelectedCount();
    connectSocket();
    drawChart();
    initMarketScene();
    updateSceneReadouts();

    if (window.lucide) {
        window.lucide.createIcons();
    }

    window.setInterval(updateUptime, 1000);
    window.addEventListener("resize", () => {
        drawChart();
        resizeMarketScene();
    });
});

function bindControls() {
    document.getElementById("startButton").addEventListener("click", startScan);
    document.getElementById("stopButton").addEventListener("click", stopScan);
    document.getElementById("addSymbolButton").addEventListener("click", addCustomSymbol);
    document.getElementById("refreshSignalsButton").addEventListener("click", fetchSignalHistory);
    document.querySelectorAll("[data-theme-option]").forEach((button) => {
        button.addEventListener("click", () => setTheme(button.dataset.themeOption));
    });
    document.getElementById("customSymbol").addEventListener("keydown", (event) => {
        if (event.key === "Enter") {
            event.preventDefault();
            addCustomSymbol();
        }
    });
    document.getElementById("symbolList").addEventListener("change", updateSelectedCount);
}

function initTheme() {
    const theme = document.documentElement.dataset.theme === "dark" ? "dark" : "light";
    setTheme(theme, { persist: false });
}

function setTheme(theme, options = {}) {
    const nextTheme = theme === "dark" ? "dark" : "light";
    state.theme = nextTheme;
    document.documentElement.dataset.theme = nextTheme;
    if (options.persist !== false) {
        localStorage.setItem("deriv-dashboard-theme", nextTheme);
    }

    document.querySelectorAll("[data-theme-option]").forEach((button) => {
        button.setAttribute("aria-pressed", String(button.dataset.themeOption === nextTheme));
    });

    drawChart();
    applyMarketSceneTheme();
}

function connectSocket() {
    if (!window.io) {
        setTransport("Socket client unavailable");
        return;
    }

    state.socket = window.io();
    state.socket.on("connect", () => setTransport("Connected"));
    state.socket.on("disconnect", () => setTransport("Disconnected"));
    state.socket.on("scanner_status", updateScannerStatus);
    state.socket.on("candle_update", handleCandle);
    state.socket.on("signal_generated", handleSignal);
    state.socket.on("signal_history", (signals) => {
        state.signals = Array.isArray(signals) ? signals : [];
        renderSignals();
    });
    state.socket.on("symbol_catalog", mergeSymbolCatalog);
    state.socket.on("scanner_log", (entry) => {
        if (entry && entry.message) {
            setTransport(entry.message);
        }
    });
}

function startScan() {
    if (!state.socket) {
        return;
    }

    const symbols = selectedSymbols();
    if (!symbols.length) {
        setTransport("Select at least one symbol");
        return;
    }

    state.socket.emit("start_scan", { symbols });
}

function stopScan() {
    if (state.socket) {
        state.socket.emit("stop_scan");
    }
}

function addCustomSymbol() {
    const input = document.getElementById("customSymbol");
    const symbol = input.value.trim().toUpperCase();
    if (!symbol || hasSymbol(symbol)) {
        input.value = "";
        return;
    }

    const label = document.createElement("label");
    label.className = "symbol-check";
    label.innerHTML = `
        <input type="checkbox" value="${escapeHtml(symbol)}" checked>
        <span>${escapeHtml(symbol)}</span>
        <strong>${escapeHtml(symbol)}</strong>
    `;
    document.getElementById("symbolList").appendChild(label);
    input.value = "";
    updateSelectedCount();
}

function selectedSymbols() {
    return Array.from(document.querySelectorAll("#symbolList input[type='checkbox']:checked"))
        .map((checkbox) => checkbox.value.trim().toUpperCase())
        .filter(Boolean);
}

function hasSymbol(symbol) {
    return Array.from(document.querySelectorAll("#symbolList input[type='checkbox']"))
        .some((checkbox) => checkbox.value.trim().toUpperCase() === symbol);
}

function updateSelectedCount() {
    const count = selectedSymbols().length;
    document.getElementById("selectedCount").textContent = `${count} selected`;
    document.getElementById("symbolsMetric").textContent = String(count);
    updateSceneReadouts();
}

function updateScannerStatus(status) {
    state.scanner = status || {};
    state.startedAt = state.scanner.started_at ? new Date(state.scanner.started_at) : null;

    const running = Boolean(state.scanner.running);
    const connected = Boolean(state.scanner.connected);
    const hasError = Boolean(state.scanner.last_error);
    const statusDot = document.getElementById("statusDot");
    const statusText = document.getElementById("statusText");
    const connectionText = document.getElementById("connectionText");

    statusDot.classList.toggle("running", running && connected);
    statusDot.classList.toggle("error", hasError);
    statusText.textContent = titleCase(state.scanner.status || "idle");
    connectionText.textContent = hasError
        ? state.scanner.last_error
        : connected
            ? "Connected to Deriv candle feed"
            : "Not connected";

    document.getElementById("startButton").disabled = running;
    document.getElementById("stopButton").disabled = !running;
    document.getElementById("candlesMetric").textContent = formatNumber(state.scanner.candles_received || 0);
    document.getElementById("signalsMetric").textContent = formatNumber(state.scanner.signals_generated || state.signals.length || 0);
    document.getElementById("gmailMetric").textContent = state.scanner.gmail_alerts_configured ? "Ready" : "Off";

    if (Array.isArray(state.scanner.symbols) && state.scanner.symbols.length) {
        document.getElementById("symbolsMetric").textContent = String(state.scanner.symbols.length);
    }
    updateSceneReadouts();
}

function setTransport(text) {
    document.getElementById("transportBadge").textContent = text;
}

function handleCandle(candle) {
    if (!candle || !candle.symbol) {
        return;
    }

    state.candles.set(candle.symbol, candle);
    state.histories.set(candle.symbol, Array.isArray(candle.history) ? candle.history : []);
    document.getElementById("lastCandleAt").textContent = `Last 15M close ${formatEpoch(candle.open_time)}`;

    renderCandles();
    drawChart();
    updateMarketSceneData();
    updateSceneReadouts();
}

function handleSignal(signal) {
    state.signals = [signal, ...state.signals.filter((item) => item.id !== signal.id)].slice(0, 100);
    renderSignals();
    triggerSignalPulse();
    updateSceneReadouts();
}

function hydrateInitialSignals() {
    state.signals = Array.isArray(window.INITIAL_SIGNALS) ? window.INITIAL_SIGNALS : [];
    renderSignals();
    updateSceneReadouts();
}

async function fetchSignalHistory() {
    const response = await fetch("/api/signals");
    state.signals = await response.json();
    renderSignals();
    updateSceneReadouts();
}

function renderCandles() {
    const body = document.getElementById("candlesBody");
    const rows = Array.from(state.candles.values())
        .sort((a, b) => a.symbol.localeCompare(b.symbol))
        .map((item) => {
            const context = item.context || {};
            const latest = item.latest_15m || {};
            return `
                <tr>
                    <td>
                        <div class="symbol-cell">
                            <strong>${escapeHtml(item.display_name || item.symbol)}</strong>
                            <span>${escapeHtml(item.symbol)}</span>
                        </div>
                    </td>
                    <td class="price-value">${formatPrice(latest.close || item.price)}</td>
                    <td>${formatEpoch(latest.open_time || item.open_time)}</td>
                    <td><span class="trend-badge ${escapeHtml(context.trend || "waiting")}">${escapeHtml(titleCase(context.trend || "waiting"))}</span></td>
                    <td><span class="market-badge ${escapeHtml(context.market_state || "sideways")}">${escapeHtml(titleCase(context.market_state || "sideways"))}</span></td>
                    <td>${setupText(context)}</td>
                </tr>
            `;
        });

    body.innerHTML = rows.length
        ? rows.join("")
        : `<tr class="empty-row"><td colspan="6">Waiting for candle data</td></tr>`;
}

function setupText(context) {
    if (!context || !context.entry_support || !context.entry_resistance) {
        return "Loading zones";
    }
    if (context.market_state === "sideways") {
        return "No trade: 15M sideways";
    }
    if (context.pending_bos) {
        return `${escapeHtml(context.pending_bos.direction)} BOS pending retest`;
    }
    if (context.trend === "uptrend") {
        return "Watching 15M resistance break";
    }
    if (context.trend === "downtrend") {
        return "Watching 15M support break";
    }
    return "Waiting for 1H direction";
}

function renderSignals() {
    const body = document.getElementById("signalsBody");
    const signals = state.signals.slice(0, 100);
    document.getElementById("signalCountLabel").textContent = signals.length
        ? `${signals.length} recent signal${signals.length === 1 ? "" : "s"}`
        : "No signals stored";

    if (!signals.length) {
        body.innerHTML = `<tr class="empty-row"><td colspan="7">No signals generated yet</td></tr>`;
        return;
    }

    body.innerHTML = signals.map((signal) => {
        const direction = String(signal.direction || "").toLowerCase();
        const confidence = Number(signal.confidence || 0);
        const indicators = signal.indicators || {};
        return `
            <tr>
                <td>${formatTime(signal.created_at)}</td>
                <td>
                    <div class="symbol-cell">
                        <strong>${escapeHtml(signal.display_name || signal.symbol)}</strong>
                        <span>${escapeHtml(signal.symbol)}</span>
                    </div>
                </td>
                <td><span class="direction-badge ${direction}">${escapeHtml(signal.direction)}</span></td>
                <td>${Number(indicators.duration_minutes || 5)}m</td>
                <td>
                    <strong>${confidence.toFixed(1)}%</strong>
                    <div class="confidence-bar"><span style="width: ${Math.min(confidence, 100)}%"></span></div>
                </td>
                <td class="price-value">${formatPrice(signal.price)}</td>
                <td>
                    <div class="setup-cell">
                        <strong>${escapeHtml(indicators.confirmation || "Strategy match")}</strong>
                        <span>${escapeHtml(signal.reason || "")}</span>
                    </div>
                </td>
            </tr>
        `;
    }).join("");

    document.getElementById("signalsMetric").textContent = formatNumber(signals.length);
}

function drawChart() {
    const canvas = document.getElementById("priceChart");
    if (!canvas) {
        return;
    }

    const rect = canvas.getBoundingClientRect();
    const ratio = window.devicePixelRatio || 1;
    const width = Math.max(rect.width, 320);
    const height = Math.max(rect.height, 260);
    canvas.width = Math.floor(width * ratio);
    canvas.height = Math.floor(height * ratio);

    const ctx = canvas.getContext("2d");
    ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
    ctx.clearRect(0, 0, width, height);
    ctx.fillStyle = cssVar("--chart-bg", "#fbfdff");
    ctx.fillRect(0, 0, width, height);

    drawGrid(ctx, width, height);

    const series = Array.from(state.histories.entries())
        .filter(([, values]) => values.length > 1)
        .slice(0, 6);

    if (!series.length) {
        ctx.fillStyle = cssVar("--muted", "#667085");
        ctx.font = "14px Inter, system-ui, sans-serif";
        ctx.textAlign = "center";
        ctx.fillText("Waiting for 15M candle history", width / 2, height / 2);
        return;
    }

    const flat = series.flatMap(([, values]) => values);
    const min = Math.min(...flat);
    const max = Math.max(...flat);
    const range = max - min || 1;
    const pad = 24;
    const palette = chartPalette();

    series.forEach(([symbol, values], index) => {
        ctx.beginPath();
        ctx.strokeStyle = palette[index % palette.length];
        ctx.lineWidth = 2;

        values.forEach((value, valueIndex) => {
            const x = pad + (valueIndex / Math.max(values.length - 1, 1)) * (width - pad * 2);
            const y = height - pad - ((value - min) / range) * (height - pad * 2);
            if (valueIndex === 0) {
                ctx.moveTo(x, y);
            } else {
                ctx.lineTo(x, y);
            }
        });
        ctx.stroke();

        ctx.fillStyle = palette[index % palette.length];
        ctx.font = "12px Inter, system-ui, sans-serif";
        ctx.textAlign = "left";
        ctx.fillText(symbol, pad + 6, 20 + index * 18);
    });
}

function drawGrid(ctx, width, height) {
    ctx.strokeStyle = cssVar("--line", "#e7edf4");
    ctx.lineWidth = 1;
    for (let index = 1; index < 5; index += 1) {
        const y = (height / 5) * index;
        ctx.beginPath();
        ctx.moveTo(16, y);
        ctx.lineTo(width - 16, y);
        ctx.stroke();
    }
}

function initMarketScene() {
    const canvas = document.getElementById("marketScene");
    if (!canvas || !window.THREE) {
        return;
    }

    const THREE = window.THREE;
    sceneState.canvas = canvas;
    sceneState.scene = new THREE.Scene();
    sceneState.camera = new THREE.PerspectiveCamera(38, 1, 0.1, 100);
    sceneState.camera.position.set(0, 8.5, 22);

    sceneState.renderer = new THREE.WebGLRenderer({
        canvas,
        antialias: true,
        alpha: false,
        preserveDrawingBuffer: true,
        powerPreference: "high-performance",
    });
    sceneState.renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
    if (THREE.SRGBColorSpace) {
        sceneState.renderer.outputColorSpace = THREE.SRGBColorSpace;
    }

    sceneState.root = new THREE.Group();
    sceneState.scene.add(sceneState.root);

    const ambient = new THREE.AmbientLight(0xffffff, 0.72);
    const keyLight = new THREE.DirectionalLight(0xffffff, 1.15);
    keyLight.position.set(-5, 10, 7);
    const sideLight = new THREE.DirectionalLight(0xffffff, 0.42);
    sideLight.position.set(7, 5, -4);
    sceneState.scene.add(ambient, keyLight, sideLight);

    sceneState.grid = new THREE.GridHelper(38, 24);
    sceneState.grid.material.transparent = true;
    sceneState.grid.material.opacity = 0.38;
    sceneState.root.add(sceneState.grid);

    sceneState.floor = new THREE.Mesh(
        new THREE.PlaneGeometry(42, 30),
        new THREE.MeshStandardMaterial({
            transparent: true,
            opacity: 0.13,
            roughness: 0.85,
            metalness: 0.05,
        }),
    );
    sceneState.floor.rotation.x = -Math.PI / 2;
    sceneState.floor.position.y = -0.04;
    sceneState.root.add(sceneState.floor);

    const barGeometry = new THREE.BoxGeometry(0.34, 1, 0.34);
    const rows = 6;
    const columns = 12;
    for (let row = 0; row < rows; row += 1) {
        for (let column = 0; column < columns; column += 1) {
            const mesh = new THREE.Mesh(barGeometry, new THREE.MeshStandardMaterial());
            mesh.position.x = (column - (columns - 1) / 2) * 1.35;
            mesh.position.z = (row - (rows - 1) / 2) * 1.75;
            mesh.userData.base = 0.6 + Math.abs(Math.sin((row + 1) * (column + 2))) * 2.6;
            mesh.userData.targetHeight = mesh.userData.base;
            mesh.scale.y = mesh.userData.base;
            mesh.position.y = mesh.scale.y / 2;
            sceneState.bars.push(mesh);
            sceneState.root.add(mesh);
        }
    }

    sceneState.ribbonGeometry = new THREE.BufferGeometry();
    sceneState.ribbon = new THREE.Line(
        sceneState.ribbonGeometry,
        new THREE.LineBasicMaterial({ linewidth: 2 }),
    );
    sceneState.root.add(sceneState.ribbon);

    const ringGeometry = new THREE.TorusGeometry(2.4, 0.018, 8, 96);
    for (let index = 0; index < 3; index += 1) {
        const ring = new THREE.Mesh(
            ringGeometry,
            new THREE.MeshBasicMaterial({ transparent: true, opacity: 0 }),
        );
        ring.rotation.x = Math.PI / 2;
        ring.position.y = 0.08 + index * 0.04;
        ring.userData.offset = index * 0.18;
        sceneState.signalRings.push(ring);
        sceneState.root.add(ring);
    }

    canvas.addEventListener("pointermove", (event) => {
        const rect = canvas.getBoundingClientRect();
        sceneState.pointer.x = ((event.clientX - rect.left) / rect.width - 0.5) * 2;
        sceneState.pointer.y = ((event.clientY - rect.top) / rect.height - 0.5) * 2;
    });
    canvas.addEventListener("pointerleave", () => {
        sceneState.pointer.x = 0;
        sceneState.pointer.y = 0;
    });

    sceneState.resizeObserver = new ResizeObserver(resizeMarketScene);
    sceneState.resizeObserver.observe(canvas);
    sceneState.ready = true;
    resizeMarketScene();
    applyMarketSceneTheme();
    updateMarketSceneData();
    animateMarketScene();
}

function applyMarketSceneTheme() {
    if (!sceneState.ready || !window.THREE) {
        return;
    }

    const THREE = window.THREE;
    const sceneBg = cssVar("--scene-bg", "#eaf2ef");
    const gridColor = cssVar("--scene-grid", "#7fa49b");
    sceneState.scene.background = new THREE.Color(sceneBg);
    sceneState.scene.fog = new THREE.Fog(sceneBg, 18, 54);
    sceneState.renderer.setClearColor(sceneBg, 1);

    sceneState.materials = {
        rise: new THREE.MeshStandardMaterial({
            color: cssVar("--rise", "#12805c"),
            roughness: 0.56,
            metalness: 0.16,
        }),
        fall: new THREE.MeshStandardMaterial({
            color: cssVar("--fall", "#c2413a"),
            roughness: 0.56,
            metalness: 0.16,
        }),
        neutral: new THREE.MeshStandardMaterial({
            color: cssVar("--blue", "#2563eb"),
            roughness: 0.62,
            metalness: 0.12,
        }),
    };

    if (sceneState.grid && sceneState.grid.material) {
        sceneState.grid.material.color.set(gridColor);
    }
    if (sceneState.floor && sceneState.floor.material) {
        sceneState.floor.material.color.set(cssVar("--teal", "#0f766e"));
    }
    if (sceneState.ribbon && sceneState.ribbon.material) {
        sceneState.ribbon.material.color.set(cssVar("--amber", "#b76e00"));
    }
    sceneState.signalRings.forEach((ring) => {
        ring.material.color.set(cssVar("--amber", "#b76e00"));
    });

    updateMarketSceneData();
}

function updateMarketSceneData() {
    if (!sceneState.ready || !window.THREE) {
        return;
    }

    const values = latestHistoryValues();
    const source = values.length ? values : seededSceneValues(72);
    const min = Math.min(...source);
    const max = Math.max(...source);
    const range = max - min || 1;
    const normalized = source.map((value) => (value - min) / range);

    sceneState.bars.forEach((bar, index) => {
        const value = normalized[index % normalized.length];
        const previous = normalized[Math.max(0, (index - 1) % normalized.length)];
        const height = 0.55 + value * 4.9;
        bar.userData.targetHeight = height;
        bar.material = value > previous ? sceneState.materials.rise : value < previous ? sceneState.materials.fall : sceneState.materials.neutral;
    });

    updateRibbonGeometry(normalized.slice(-52));
}

function updateRibbonGeometry(values) {
    if (!sceneState.ribbonGeometry || !window.THREE) {
        return;
    }

    const points = [];
    const count = Math.max(values.length, 2);
    for (let index = 0; index < count; index += 1) {
        const value = values[index] ?? values[values.length - 1] ?? 0.5;
        const x = -15 + (index / Math.max(count - 1, 1)) * 30;
        const y = 2.1 + value * 5.2;
        const z = -6.2 + Math.sin(index * 0.42) * 0.7;
        points.push(x, y, z);
    }
    sceneState.ribbonGeometry.setAttribute("position", new window.THREE.Float32BufferAttribute(points, 3));
    sceneState.ribbonGeometry.computeBoundingSphere();
}

function animateMarketScene() {
    if (!sceneState.ready) {
        return;
    }

    const camera = sceneState.camera;
    const root = sceneState.root;
    const running = Boolean(state.scanner && state.scanner.running);
    root.rotation.y += running ? 0.0022 : 0.0011;
    root.rotation.x += ((sceneState.pointer.y * 0.035) - root.rotation.x) * 0.035;

    camera.position.x += (sceneState.pointer.x * 1.4 - camera.position.x) * 0.045;
    camera.position.y += (8.2 + sceneState.pointer.y * -0.5 - camera.position.y) * 0.045;
    camera.lookAt(0, 1.8, 0);

    sceneState.bars.forEach((bar, index) => {
        const wave = Math.sin(performance.now() * 0.0016 + index * 0.37) * 0.08;
        const target = Math.max(0.2, bar.userData.targetHeight + wave);
        bar.scale.y += (target - bar.scale.y) * 0.055;
        bar.position.y = bar.scale.y / 2;
    });

    if (sceneState.pulse > 0) {
        sceneState.pulse = Math.max(0, sceneState.pulse - 0.012);
    }
    sceneState.signalRings.forEach((ring) => {
        const amount = Math.max(0, sceneState.pulse - ring.userData.offset);
        ring.scale.setScalar(1 + (1 - amount) * 2.1);
        ring.material.opacity = amount * 0.42;
    });

    sceneState.renderer.render(sceneState.scene, camera);
    window.requestAnimationFrame(animateMarketScene);
}

function resizeMarketScene() {
    if (!sceneState.ready || !sceneState.canvas) {
        return;
    }

    const rect = sceneState.canvas.getBoundingClientRect();
    const width = Math.max(1, Math.floor(rect.width));
    const height = Math.max(1, Math.floor(rect.height));
    sceneState.renderer.setSize(width, height, false);
    sceneState.camera.aspect = width / height;
    sceneState.camera.updateProjectionMatrix();
}

function triggerSignalPulse() {
    sceneState.pulse = 1;
}

function latestHistoryValues() {
    const histories = Array.from(state.histories.values())
        .filter((values) => Array.isArray(values) && values.length > 2)
        .sort((left, right) => right.length - left.length);
    return histories.length ? histories[0].slice(-72).map(Number).filter(Number.isFinite) : [];
}

function seededSceneValues(count) {
    return Array.from({ length: count }, (_, index) => (
        52
        + Math.sin(index * 0.33) * 16
        + Math.cos(index * 0.11) * 9
        + Math.sin(index * 0.71) * 4
    ));
}

function updateSceneReadouts() {
    const candles = Number(state.scanner && state.scanner.candles_received ? state.scanner.candles_received : 0);
    const signals = Number(
        state.scanner && state.scanner.signals_generated
            ? state.scanner.signals_generated
            : state.signals.length,
    );
    const selected = selectedSymbols().length;
    const headline = document.getElementById("sceneHeadline");
    const status = document.getElementById("sceneStatus");
    const candlesOutput = document.getElementById("sceneCandles");
    const signalsOutput = document.getElementById("sceneSignals");

    if (candlesOutput) {
        candlesOutput.textContent = formatNumber(candles);
    }
    if (signalsOutput) {
        signalsOutput.textContent = formatNumber(signals);
    }
    if (!headline || !status) {
        return;
    }

    if (state.scanner && state.scanner.running) {
        const scannerSymbols = Array.isArray(state.scanner.symbols) ? state.scanner.symbols.length : 0;
        headline.textContent = state.scanner.connected ? `Scanning ${selected || scannerSymbols} symbols` : "Connecting scanner";
        status.textContent = state.scanner.last_error || "Live 1H trend and 15M setup data are flowing into the terrain";
    } else if (state.candles.size) {
        headline.textContent = `${state.candles.size} symbols cached`;
        status.textContent = "Recent candle structure is ready for the next scan";
    } else {
        headline.textContent = "Scanner idle";
        status.textContent = `${selected} symbols selected`;
    }
}

function mergeSymbolCatalog(symbols) {
    if (!Array.isArray(symbols)) {
        return;
    }

    symbols.forEach((item) => {
        if (!item.symbol || hasSymbol(item.symbol)) {
            return;
        }
        const label = document.createElement("label");
        label.className = "symbol-check";
        label.innerHTML = `
            <input type="checkbox" value="${escapeHtml(item.symbol)}">
            <span>${escapeHtml(item.display_name || item.symbol)}</span>
            <strong>${escapeHtml(item.symbol)}</strong>
        `;
        document.getElementById("symbolList").appendChild(label);
    });
    updateSelectedCount();
}

function updateUptime() {
    const output = document.getElementById("uptimeMetric");
    if (!state.startedAt || !state.scanner || !state.scanner.running) {
        output.textContent = "00:00";
        return;
    }

    const totalSeconds = Math.max(0, Math.floor((Date.now() - state.startedAt.getTime()) / 1000));
    const minutes = Math.floor(totalSeconds / 60);
    const seconds = totalSeconds % 60;
    output.textContent = `${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}`;
}

function chartPalette() {
    return [
        cssVar("--teal", "#0f766e"),
        cssVar("--blue", "#2563eb"),
        cssVar("--amber", "#b76e00"),
        cssVar("--fall", "#c2413a"),
        "#8b5cf6",
        cssVar("--rise", "#12805c"),
    ];
}

function cssVar(name, fallback = "") {
    const value = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
    return value || fallback;
}

function formatPrice(value) {
    const number = Number(value);
    if (!Number.isFinite(number)) {
        return "-";
    }
    return number.toLocaleString(undefined, { maximumFractionDigits: 5 });
}

function formatNumber(value) {
    return Number(value || 0).toLocaleString();
}

function formatEpoch(epoch) {
    if (!epoch) {
        return "-";
    }
    return new Date(Number(epoch) * 1000).toLocaleTimeString([], {
        hour: "2-digit",
        minute: "2-digit",
        second: "2-digit",
    });
}

function formatTime(value) {
    if (!value) {
        return "-";
    }
    return new Date(value).toLocaleTimeString([], {
        hour: "2-digit",
        minute: "2-digit",
        second: "2-digit",
    });
}

function titleCase(value) {
    return String(value)
        .replace(/_/g, " ")
        .replace(/\w\S*/g, (text) => text.charAt(0).toUpperCase() + text.slice(1).toLowerCase());
}

function escapeHtml(value) {
    return String(value)
        .replaceAll("&", "&amp;")
        .replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;")
        .replaceAll('"', "&quot;")
        .replaceAll("'", "&#039;");
}

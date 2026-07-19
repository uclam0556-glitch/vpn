const tg = window.Telegram.WebApp;

// Setup Telegram Web App
tg.expand();
tg.ready();

// Try to set the header color to match the dark theme
try {
    tg.setHeaderColor('#0f172a');
    tg.setBackgroundColor('#0f172a');
} catch(e) {}

// Configuration
const API_BASE = '/api/tma';
let userData = null;

// DOM Elements
const views = document.querySelectorAll('.view');
const navBtns = document.querySelectorAll('.nav-btn');

// Haptic feedback helper
function hapticLight() {
    if (tg.HapticFeedback) tg.HapticFeedback.impactOccurred('light');
}
function hapticMedium() {
    if (tg.HapticFeedback) tg.HapticFeedback.impactOccurred('medium');
}
function hapticSuccess() {
    if (tg.HapticFeedback) tg.HapticFeedback.notificationOccurred('success');
}
function hapticError() {
    if (tg.HapticFeedback) tg.HapticFeedback.notificationOccurred('error');
}

// Show specific view
function showView(viewId) {
    views.forEach(v => v.classList.remove('active'));
    navBtns.forEach(b => b.classList.remove('active'));

    document.getElementById(`view-${viewId}`).classList.add('active');
    document.querySelector(`.nav-btn[data-target="${viewId}"]`).classList.add('active');
}

// Navigation Listeners
navBtns.forEach(btn => {
    btn.addEventListener('click', () => {
        const target = btn.dataset.target;
        showView(target);
        if (target === 'devices') loadDevices();
        if (target === 'referrals') loadReferrals();
        hapticLight();
    });
});

// Modal Logic
const modalOverlay = document.getElementById('modal-overlay');
const modalTitle = document.getElementById('modal-title');
const modalText = document.getElementById('modal-text');

function showModal(title, text) {
    modalTitle.textContent = title;
    modalText.textContent = text;
    modalOverlay.classList.add('active');
    hapticMedium();
}

document.getElementById('btn-modal-close').addEventListener('click', () => {
    modalOverlay.classList.remove('active');
    hapticLight();
});

// API Helper
async function apiCall(endpoint, method = 'GET', body = null) {
    try {
        const headers = {
            'Content-Type': 'application/json',
            'X-Telegram-Init-Data': tg.initData || ''
        };

        const options = { method, headers };
        if (body) options.body = JSON.stringify(body);

        const res = await fetch(`${API_BASE}${endpoint}`, options);
        const data = await res.json();

        if (!res.ok) throw new Error(data.detail || 'API Error');
        return data;
    } catch (e) {
        console.error(e);
        showModal('Ошибка', e.message);
        hapticError();
        throw e;
    }
}

// Format bytes
function formatBytes(bytes) {
    if (bytes === 0) return '0 ГБ';
    const k = 1024;
    const sizes = ['Б', 'КБ', 'МБ', 'ГБ', 'ТБ'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i];
}

// Remove skeletons
function removeSkeletons(containerId) {
    const container = document.getElementById(containerId) || document;
    const skeletons = container.querySelectorAll('.skeleton');
    skeletons.forEach(el => {
        el.classList.remove('skeleton');
    });
}

// Set Progress Ring
function setProgress(percent) {
    const circle = document.getElementById('traffic-ring');
    const radius = circle.r.baseVal.value;
    const circumference = radius * 2 * Math.PI;
    circle.style.strokeDasharray = `${circumference} ${circumference}`;

    // Percent max 100
    const safePercent = Math.min(Math.max(percent, 0), 100);
    const offset = circumference - (safePercent / 100) * circumference;

    // Animate stroke
    setTimeout(() => {
        circle.style.strokeDashoffset = offset;

        // Change color based on usage
        if(safePercent > 90) circle.style.stroke = 'var(--danger)';
        else if(safePercent > 70) circle.style.stroke = 'var(--warning)';
        else circle.style.stroke = 'var(--primary-color)';
    }, 100);
}

// Load Home Data
async function loadHome() {
    try {
        const data = await apiCall('/me');
        userData = data;

        removeSkeletons('view-home');

        // Update Status Badge
        const statusBadge = document.getElementById('status-badge');
        const statusText = document.getElementById('status-text');
        const daysLeftBadge = document.getElementById('days-left-badge');

        if (data.status === 'active') {
            statusBadge.className = 'status-badge active';
            statusText.textContent = 'Подписка активна';

            if (data.expire_at) {
                const days = Math.max(0, Math.ceil((data.expire_at * 1000 - Date.now()) / (1000 * 60 * 60 * 24)));
                daysLeftBadge.textContent = `${days} дней осталось`;
            } else {
                daysLeftBadge.textContent = 'Безлимит';
            }
        } else {
            statusBadge.className = 'status-badge inactive';
            statusText.textContent = 'Нет подписки';
            daysLeftBadge.textContent = '0 дней';
            document.getElementById('btn-renew').style.display = 'flex';
            document.getElementById('btn-smart-connect').style.display = 'none';
        }

        // Update Traffic Ring
        let trafficUsedText = formatBytes(data.used_traffic);
        document.getElementById('traffic-value').textContent = trafficUsedText.split(' ')[0];
        document.getElementById('traffic-label').textContent = trafficUsedText.split(' ')[1] + ' Использовано';

        document.getElementById('traffic-limit').textContent = data.data_limit ? formatBytes(data.data_limit) : 'Безлимит';
        document.getElementById('devices-count').textContent = `${data.active_devices} / ${data.device_limit || '∞'}`;

        // Calculate percentage for ring
        let percent = 0;
        if (data.data_limit > 0) {
            percent = (data.used_traffic / data.data_limit) * 100;
        }
        setProgress(percent);

        // Connect Button Logic
        const btnConnect = document.getElementById('btn-smart-connect');
        if (data.raw_url) {
            btnConnect.onclick = () => {
                hapticLight();

                // Detect OS
                const ua = navigator.userAgent.toLowerCase();
                const isIos = /iphone|ipad|ipod|mac OS/.test(ua);
                const isAndroid = /android/.test(ua);

                let appLinks = '';
                const encodedUrl = encodeURIComponent(data.raw_url);

                if (isIos) {
                    appLinks = `
                        <button class="nav-btn active" style="width:100%; margin-bottom:10px; padding:15px; border-radius:15px;" onclick="window.location.href='streisand://import/${encodedUrl}'">🍎 Открыть в Streisand</button>
                        <button class="nav-btn active" style="width:100%; margin-bottom:10px; padding:15px; border-radius:15px;" onclick="window.location.href='v2raytun://import/${encodedUrl}'">🍎 Открыть в v2rayTun</button>
                        <button class="nav-btn active" style="width:100%; padding:15px; border-radius:15px;" onclick="window.location.href='hiddify://install-config?url=${encodedUrl}'">🦊 Открыть в Hiddify</button>
                    `;
                } else if (isAndroid) {
                    appLinks = `
                        <button class="nav-btn active" style="width:100%; margin-bottom:10px; padding:15px; border-radius:15px;" onclick="window.location.href='v2rayng://install-config?url=${encodedUrl}'">🤖 Открыть в v2rayNG</button>
                        <button class="nav-btn active" style="width:100%; padding:15px; border-radius:15px;" onclick="window.location.href='hiddify://install-config?url=${encodedUrl}'">🦊 Открыть в Hiddify</button>
                    `;
                } else {
                    appLinks = `
                        <button class="nav-btn active" style="width:100%; margin-bottom:10px; padding:15px; border-radius:15px;" onclick="window.location.href='hiddify://install-config?url=${encodedUrl}'">🦊 Открыть в Hiddify (ПК)</button>
                        <button class="nav-btn active" style="width:100%; padding:15px; border-radius:15px;" onclick="tg.openLink('${data.connect_url}')">🌍 Открыть страницу настройки</button>
                    `;
                }

                // We reuse the existing modal
                modalTitle.textContent = 'Выберите приложение';
                modalText.innerHTML = '<p style="margin-bottom:20px; color:var(--text-secondary);">В какое приложение добавить вашу подписку?</p>' + appLinks;
                modalOverlay.classList.add('active');
                hapticMedium();
            };
        } else {
            btnConnect.style.display = 'none';
        }

    } catch (e) {
        console.error("Home error", e);
    }
}

// Renew Subscription
document.getElementById('btn-renew').addEventListener('click', () => {
    hapticMedium();
    tg.close();
});

// Load Devices
async function loadDevices() {
    try {
        const devicesList = document.getElementById('devices-list');
        // Reset to skeletons
        devicesList.innerHTML = '<div class="device-item skeleton" style="height: 80px;"></div><div class="device-item skeleton" style="height: 80px;"></div>';

        const devices = await apiCall('/devices');
        devicesList.innerHTML = '';

        if (devices.length === 0) {
            devicesList.innerHTML = '<p style="text-align: center; color: var(--text-secondary); margin-top: 20px;">Нет активных устройств</p>';
            return;
        }

        devices.forEach(d => {
            const el = document.createElement('div');
            el.className = 'device-item';

            // OS icon logic
            let icon = '📱';
            let label = d.name || 'Неизвестно';
            if (label.toLowerCase().includes('ios') || label.toLowerCase().includes('iphone')) icon = '🍎';
            if (label.toLowerCase().includes('android')) icon = '🤖';
            if (label.toLowerCase().includes('windows')) icon = '💻';

            el.innerHTML = `
                <div class="device-info">
                    <div class="device-icon">${icon}</div>
                    <div>
                        <div class="device-name">${label}</div>
                        <div class="device-ip">IP: ${d.last_ip || 'Скрыт'}</div>
                    </div>
                </div>
                <button class="btn-revoke" onclick="revokeDevice('${d.id}')">Отключить</button>
            `;
            devicesList.appendChild(el);
        });
    } catch (e) {
        console.error("Devices error", e);
    }
}

// Revoke Device
window.revokeDevice = async function(id) {
    hapticMedium();
    tg.showConfirm('Вы точно хотите отключить это устройство? Оно больше не сможет использовать VPN.', async (confirmed) => {
        if (confirmed) {
            try {
                await apiCall(`/devices/${id}`, 'DELETE');
                hapticSuccess();
                loadDevices(); // reload
            } catch (e) {
                // error handled by apiCall
            }
        }
    });
};

// Load Referrals
async function loadReferrals() {
    try {
        const data = await apiCall('/referrals');

        removeSkeletons('view-referrals');

        document.getElementById('ref-balance').textContent = `${data.balance} ₽`;
        document.getElementById('ref-count').textContent = data.total_referrals;
        document.getElementById('ref-earned').textContent = `${data.total_earned} ₽`;

        const botUsername = data.bot_username || 'HamaliVpn_bot';
        const tgId = userData ? userData.telegram_id : '';
        const refLink = `https://t.me/${botUsername}?start=ref_${tgId}`;

        document.getElementById('ref-link-text').textContent = refLink;

        // Gamification Level Logic
        const levels = [
            { max: 5, name: 'Уровень 1 (Новичок)' },
            { max: 15, name: 'Уровень 2 (Адепт)' },
            { max: 50, name: 'Уровень 3 (Мастер)' },
            { max: 99999, name: 'Уровень 4 (Амбассадор)' }
        ];

        let currentLevel = levels[0];
        for (let l of levels) {
            if (data.total_referrals < l.max) {
                currentLevel = l;
                break;
            }
        }

        document.getElementById('ref-level-text').textContent = currentLevel.name;

        const remaining = currentLevel.max - data.total_referrals;
        if (currentLevel.max === 99999) {
            document.getElementById('ref-next-text').textContent = 'Максимальный уровень!';
            document.getElementById('ref-progress-fill').style.width = '100%';
        } else {
            document.getElementById('ref-next-text').textContent = `Ещё ${remaining} до след. уровня`;
            const prevMax = levels[levels.indexOf(currentLevel) - 1]?.max || 0;
            const progress = ((data.total_referrals - prevMax) / (currentLevel.max - prevMax)) * 100;
            setTimeout(() => {
                document.getElementById('ref-progress-fill').style.width = `${progress}%`;
            }, 100);
        }

        // Share button logic
        document.getElementById('btn-share-tg').onclick = () => {
            hapticLight();
            const text = encodeURIComponent('Привет! Пользуюсь премиум VPN от Hamali. Присоединяйся и получи бонус:');
            const shareUrl = `https://t.me/share/url?url=${refLink}&text=${text}`;
            tg.openTelegramLink(shareUrl);
        };

        // Copy logic
        document.getElementById('btn-copy-ref').onclick = () => {
            navigator.clipboard.writeText(refLink).then(() => {
                hapticSuccess();
                tg.showAlert('Ссылка скопирована!');
            });
        };

    } catch (e) {
        console.error("Referrals error", e);
    }
}

// Initialization
loadHome();

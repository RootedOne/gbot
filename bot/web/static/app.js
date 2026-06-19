// Global State
const state = {
    sessionToken: localStorage.getItem('session_token') || '',
    activeTab: 'dashboard',
    charts: {
        revenue: null,
        plans: null
    },
    users: {
        search: '',
        limit: 15,
        offset: 0,
        total: 0
    },
    orders: {
        status: ''
    },
    panels: [],
    inboundsFetched: {} // panel_id -> [inbounds]
};

// API Helpers
async function apiRequest(path, options = {}) {
    const headers = {
        'Content-Type': 'application/json',
        ...options.headers
    };
    if (state.sessionToken) {
        headers['Authorization'] = `Bearer ${state.sessionToken}`;
    }

    const response = await fetch(path, { ...options, headers });
    
    if (response.status === 401 || response.status === 403) {
        // Logged out
        showLoginOverlay();
        throw new Error('Unauthorized');
    }

    if (!response.ok) {
        const errorData = await response.json().catch(() => ({}));
        throw new Error(errorData.error || `HTTP Error ${response.status}`);
    }

    return response.json();
}

// Initialise Application
document.addEventListener('DOMContentLoaded', async () => {
    setupEventListeners();
    
    // Check url parameter first
    const urlParams = new URLSearchParams(window.location.search);
    const urlToken = urlParams.get('token');
    
    if (urlToken) {
        // Try logging in with the URL token
        try {
            const data = await apiRequest(`/api/admin/auth?token=${urlToken}`);
            if (data.session_token) {
                state.sessionToken = data.session_token;
                localStorage.setItem('session_token', data.session_token);
                // Clean URL query parameter
                window.history.replaceState({}, document.title, window.location.pathname);
            }
        } catch (err) {
            console.error('URL login failed:', err);
            showLoginOverlay(err.message);
            return;
        }
    }

    if (state.sessionToken) {
        showDashboard();
    } else {
        showLoginOverlay();
    }
});

// Auth Overlays
function showLoginOverlay(errorText = '') {
    localStorage.removeItem('session_token');
    state.sessionToken = '';
    document.getElementById('dashboard-container').style.display = 'none';
    document.getElementById('login-overlay').style.display = 'flex';
    if (errorText) {
        document.getElementById('login-error').textContent = errorText;
    }
}

function showDashboard() {
    document.getElementById('login-overlay').style.display = 'none';
    document.getElementById('dashboard-container').style.display = 'flex';
    navigateToTab(state.activeTab);
}

// Tab Navigation
function navigateToTab(tabName) {
    state.activeTab = tabName;
    
    // Update sidebar UI
    document.querySelectorAll('.nav-item').forEach(item => {
        if (item.getAttribute('data-tab') === tabName) {
            item.classList.add('active');
        } else {
            item.classList.remove('active');
        }
    });

    // Update main header
    const titles = {
        dashboard: 'Dashboard',
        users: 'User Management',
        orders: 'Payment Orders & Reviews',
        plans: 'Service Plans Configuration',
        panels: '3X-UI Servers & Panels',
        nodes: 'Reseller Bot Nodes',
        settings: 'Global Config & Pricing'
    };
    document.getElementById('header-title').textContent = titles[tabName] || 'Dashboard';

    // Show active content
    document.querySelectorAll('.tab-content').forEach(content => {
        content.classList.remove('active');
    });
    
    const targetTab = document.getElementById(`tab-${tabName}`);
    if (targetTab) {
        targetTab.classList.add('active');
    }

    // Trigger tab specific data load
    loadTabData(tabName);
}

function loadTabData(tabName) {
    switch(tabName) {
        case 'dashboard':
            loadDashboardStats();
            break;
        case 'users':
            loadUsers();
            break;
        case 'orders':
            loadOrders();
            break;
        case 'plans':
            loadPlans();
            break;
        case 'panels':
            loadPanels();
            break;
        case 'nodes':
            loadNodes();
            break;
        case 'settings':
            loadSettings();
            break;
    }
}

// -------------------------------------------------------------
// Tab 1: Dashboard
// -------------------------------------------------------------
async function loadDashboardStats() {
    try {
        const data = await apiRequest('/api/admin/stats');
        
        // Render stats counters
        document.getElementById('stat-users').textContent = data.stats.users_count;
        document.getElementById('stat-services').textContent = data.stats.active_services;
        document.getElementById('stat-receipts').textContent = data.stats.pending_receipts;
        
        // Show/hide orders badge in sidebar
        const badge = document.getElementById('badge-orders');
        if (data.stats.pending_receipts > 0) {
            badge.textContent = data.stats.pending_receipts;
            badge.style.display = 'inline-block';
        } else {
            badge.style.display = 'none';
        }

        // Render revenue charts
        renderRevenueChart(data.income.periods);
        
        // Render popular plans chart
        renderPlansChart(data.income.popular_plans);

        // Render server diagnostics list
        renderDashboardServers(data.panels);

    } catch (err) {
        console.error('Failed to load dashboard metrics:', err);
    }
}

function renderRevenueChart(periods) {
    const ctx = document.getElementById('chart-revenue').getContext('2d');
    
    // Aggregate income for standard display
    const labelMapping = { 'today': 'Today', '7d': 'Last 7 Days', '30d': 'Last 30 Days', 'all_time': 'All Time' };
    const labels = Object.values(labelMapping);
    
    // Extract primary currencies
    const currencies = new Set();
    Object.values(periods).forEach(currObj => {
        Object.keys(currObj).forEach(curr => currencies.add(curr));
    });
    
    const datasets = [];
    const colors = ['#6366f1', '#a855f7', '#ec4899'];
    let colorIndex = 0;
    
    currencies.forEach(curr => {
        const data = Object.keys(labelMapping).map(key => periods[key][curr] || 0);
        datasets.push({
            label: `Revenue (${curr})`,
            data: data,
            borderColor: colors[colorIndex % colors.length],
            backgroundColor: `${colors[colorIndex % colors.length]}1A`,
            fill: true,
            tension: 0.3
        });
        colorIndex++;
    });

    if (state.charts.revenue) {
        state.charts.revenue.destroy();
    }

    state.charts.revenue = new Chart(ctx, {
        type: 'line',
        data: { labels, datasets },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: { labels: { color: '#f3f4f6' } }
            },
            scales: {
                y: { grid: { color: 'rgba(255,255,255,0.05)' }, ticks: { color: '#9ca3af' } },
                x: { grid: { color: 'rgba(255,255,255,0.05)' }, ticks: { color: '#9ca3af' } }
            }
        }
    });
}

function renderPlansChart(popularPlans) {
    const ctx = document.getElementById('chart-plans').getContext('2d');
    
    const labels = popularPlans.map(p => `${p.title} (${p.currency})`);
    const data = popularPlans.map(p => p.sales_count);
    
    if (state.charts.plans) {
        state.charts.plans.destroy();
    }

    state.charts.plans = new Chart(ctx, {
        type: 'doughnut',
        data: {
            labels,
            datasets: [{
                data,
                backgroundColor: ['#6366f1', '#8b5cf6', '#ec4899', '#10b981', '#f59e0b'],
                borderWidth: 0
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: { position: 'bottom', labels: { color: '#f3f4f6', boxWidth: 12 } }
            }
        }
    });
}

function renderDashboardServers(panels) {
    const tbody = document.getElementById('dashboard-servers-list');
    tbody.innerHTML = '';
    
    if (panels.length === 0) {
        tbody.innerHTML = `<tr><td colspan="5" class="text-center text-secondary">No servers registered yet.</td></tr>`;
        return;
    }

    panels.forEach(p => {
        let statusBadge = p.online 
            ? `<span class="badge badge-success">Online</span>` 
            : `<span class="badge badge-danger">Offline</span>`;
            
        let cpu = p.online ? `${p.cpu}%` : '—';
        let mem = p.online ? `${(p.mem * 100).toFixed(1)}%` : '—';
        let xray = p.online ? p.xray : '—';
        
        if (!p.is_active) {
            statusBadge = `<span class="badge badge-warning">Disabled</span>`;
        }

        tbody.innerHTML += `
            <tr>
                <td><strong>${p.name}</strong></td>
                <td>${statusBadge}</td>
                <td>${cpu}</td>
                <td>${mem}</td>
                <td><code class="${xray === 'running' ? 'text-success' : 'text-danger'}">${xray}</code></td>
            </tr>
        `;
    });
}

// -------------------------------------------------------------
// Tab 2: Users
// -------------------------------------------------------------
async function loadUsers() {
    try {
        const query = `/api/admin/users?search=${encodeURIComponent(state.users.search)}&limit=${state.users.limit}&offset=${state.users.offset}`;
        const data = await apiRequest(query);
        
        state.users.total = data.total;
        renderUsersTable(data.users);
        updatePaginationUI();
    } catch (err) {
        console.error('Failed to load users:', err);
    }
}

function renderUsersTable(users) {
    const tbody = document.getElementById('users-list');
    tbody.innerHTML = '';
    
    if (users.length === 0) {
        tbody.innerHTML = `<tr><td colspan="7" class="text-center text-secondary">No matching users found.</td></tr>`;
        return;
    }

    users.forEach(u => {
        const statusBadge = u.is_blocked 
            ? `<span class="badge badge-danger">Blocked</span>` 
            : `<span class="badge badge-success">Active</span>`;
            
        const resellerBadge = u.is_reseller 
            ? `<span class="badge badge-reseller">Reseller (${u.reseller_gb_price} T/GB / ${u.reseller_unlimited_price || 0} T/♾️)</span>` 
            : `<span class="badge badge-warning">Retail</span>`;

        tbody.innerHTML += `
            <tr>
                <td><code>${u.tg_id}</code></td>
                <td>${u.full_name || '—'}</td>
                <td>${u.username ? '@' + u.username : '—'}</td>
                <td><strong>${parseInt(u.balance).toLocaleString()} Tomans</strong></td>
                <td>${resellerBadge}</td>
                <td>${statusBadge}</td>
                <td>
                    <button class="btn btn-secondary btn-sm" onclick="openBalanceModal(${u.tg_id}, '${u.full_name || u.tg_id}')">
                        <i class="fa-solid fa-wallet"></i> Balance
                    </button>
                    <button class="btn btn-secondary btn-sm" onclick="openResellerModal(${u.tg_id}, '${u.full_name || u.tg_id}', ${u.is_reseller}, ${u.reseller_gb_price}, ${u.reseller_day_price}, ${u.reseller_unlimited_price || 0})">
                        <i class="fa-solid fa-briefcase"></i> Reseller
                    </button>
                    <button class="btn ${u.is_blocked ? 'btn-success' : 'btn-danger'} btn-sm" onclick="toggleUserBlock(${u.tg_id}, ${u.is_blocked})">
                        <i class="fa-solid ${u.is_blocked ? 'fa-user-check' : 'fa-user-slash'}"></i> ${u.is_blocked ? 'Unblock' : 'Block'}
                    </button>
                </td>
            </tr>
        `;
    });
}

function updatePaginationUI() {
    const pageNum = Math.floor(state.users.offset / state.users.limit) + 1;
    const totalPages = Math.ceil(state.users.total / state.users.limit) || 1;
    
    document.getElementById('users-page-info').textContent = `Page ${pageNum} of ${totalPages}`;
    document.getElementById('btn-users-prev').disabled = state.users.offset === 0;
    document.getElementById('btn-users-next').disabled = (state.users.offset + state.users.limit) >= state.users.total;
}

async function toggleUserBlock(tgId, currentBlockState) {
    if (!confirm(`Are you sure you want to ${currentBlockState ? 'UNBLOCK' : 'BLOCK'} this user?`)) return;
    try {
        await apiRequest(`/api/admin/users/${tgId}/toggle-block`, {
            method: 'POST',
            body: JSON.stringify({ blocked: !currentBlockState })
        });
        loadUsers();
    } catch (err) {
        alert(err.message);
    }
}

// -------------------------------------------------------------
// Tab 3: Orders & Reviews
// -------------------------------------------------------------
async function loadOrders() {
    try {
        const query = `/api/admin/orders?status=${state.orders.status}&limit=40`;
        const data = await apiRequest(query);
        renderOrdersTable(data.orders);
    } catch (err) {
        console.error('Failed to load orders:', err);
    }
}

function renderOrdersTable(orders) {
    const tbody = document.getElementById('orders-list');
    tbody.innerHTML = '';
    
    if (orders.length === 0) {
        tbody.innerHTML = `<tr><td colspan="9" class="text-center text-secondary">No orders found.</td></tr>`;
        return;
    }

    orders.forEach(o => {
        let statusBadgeClass = 'badge-warning';
        if (o.status === 'paid') statusBadgeClass = 'badge-success';
        if (o.status === 'rejected' || o.status === 'cancelled') statusBadgeClass = 'badge-danger';
        
        const statusBadge = `<span class="badge ${statusBadgeClass}">${o.status}</span>`;
        const dateStr = new Date(o.created_at).toLocaleString();
        
        let actionBtn = '';
        if (o.status === 'awaiting_review') {
            actionBtn = `
                <button class="btn btn-primary btn-sm" onclick="openReceiptModal(${o.id})">
                    <i class="fa-solid fa-magnifying-glass"></i> Review Receipt
                </button>
            `;
        }

        tbody.innerHTML += `
            <tr>
                <td><code>#${o.id}</code></td>
                <td><code>${o.user_tg_id}</code></td>
                <td>${o.plan_title}</td>
                <td><code>${o.kind}</code></td>
                <td>${o.method || '—'}</td>
                <td><strong>${parseInt(o.amount).toLocaleString()} ${o.currency}</strong></td>
                <td>${statusBadge}</td>
                <td>${dateStr}</td>
                <td>${actionBtn}</td>
            </tr>
        `;
    });
}

// -------------------------------------------------------------
// Tab 4: Plans
// -------------------------------------------------------------
async function loadPlans() {
    try {
        const plans = await apiRequest('/api/admin/plans');
        const tbody = document.getElementById('plans-list');
        tbody.innerHTML = '';
        
        if (plans.length === 0) {
            tbody.innerHTML = `<tr><td colspan="9" class="text-center text-secondary">No service plans configured.</td></tr>`;
            return;
        }

        plans.forEach(p => {
            const statusBadge = p.is_active 
                ? `<span class="badge badge-success">Active</span>` 
                : `<span class="badge badge-danger">Disabled</span>`;
                
            const trialBadge = p.is_trial ? `<span class="badge badge-warning">Trial</span>` : '';

            tbody.innerHTML += `
                <tr>
                    <td><code>${p.sort_order}</code></td>
                    <td><strong>${p.title}</strong> ${trialBadge}</td>
                    <td>${p.traffic_gb === 0 ? 'Unlimited' : p.traffic_gb + ' GB'}</td>
                    <td>${p.duration_days === 0 ? 'Never Expires' : p.duration_days + ' Days'}</td>
                    <td>${parseInt(p.price_fiat).toLocaleString()}</td>
                    <td>${p.price_stars} Stars</td>
                    <td>$${p.price_usd}</td>
                    <td>${statusBadge}</td>
                    <td>
                        <button class="btn btn-secondary btn-sm" onclick="openPlanModal(${p.id})">
                            <i class="fa-solid fa-pen-to-square"></i> Edit
                        </button>
                        <button class="btn btn-danger btn-sm" onclick="deletePlan(${p.id})">
                            <i class="fa-solid fa-trash"></i> Delete
                        </button>
                    </td>
                </tr>
            `;
        });
    } catch (err) {
        console.error('Failed to load plans:', err);
    }
}

async function deletePlan(planId) {
    if (!confirm('Are you sure you want to delete this plan? This cannot be undone.')) return;
    try {
        await apiRequest(`/api/admin/plans/${planId}`, { method: 'DELETE' });
        loadPlans();
    } catch (err) {
        alert(err.message);
    }
}

// -------------------------------------------------------------
// Tab 5: Servers (Panels)
// -------------------------------------------------------------
async function loadPanels() {
    try {
        const panels = await apiRequest('/api/admin/panels');
        state.panels = panels;
        const tbody = document.getElementById('panels-list');
        tbody.innerHTML = '';
        
        if (panels.length === 0) {
            tbody.innerHTML = `<tr><td colspan="9" class="text-center text-secondary">No servers configured yet.</td></tr>`;
            return;
        }

        panels.forEach(p => {
            tbody.innerHTML += `
                <tr>
                    <td><code>${p.sort_order}</code></td>
                    <td><strong>${p.name}</strong></td>
                    <td><code>${p.base_url}</code></td>
                    <td><span class="badge ${p.is_active ? 'badge-success' : 'badge-danger'}">${p.is_active ? 'Yes' : 'No'}</span></td>
                    <td><span class="badge ${p.allow_trials ? 'badge-success' : 'badge-danger'}">${p.allow_trials ? 'Yes' : 'No'}</span></td>
                    <td><span class="badge ${p.allow_migrations ? 'badge-success' : 'badge-danger'}">${p.allow_migrations ? 'Yes' : 'No'}</span></td>
                    <td><span class="badge ${p.allow_resellers ? 'badge-success' : 'badge-danger'}">${p.allow_resellers ? 'Yes' : 'No'}</span></td>
                    <td>
                        <button class="btn btn-secondary btn-sm" onclick="testPanelConnection(${p.id})">
                            <i class="fa-solid fa-plug"></i> Test
                        </button>
                    </td>
                    <td>
                        <button class="btn btn-secondary btn-sm" onclick="openPanelModal(${p.id})">
                            <i class="fa-solid fa-pen-to-square"></i> Edit
                        </button>
                        <button class="btn btn-danger btn-sm" onclick="deletePanel(${p.id})">
                            <i class="fa-solid fa-trash"></i> Delete
                        </button>
                    </td>
                </tr>
            `;
        });
    } catch (err) {
        console.error('Failed to load servers:', err);
    }
}

async function testPanelConnection(id) {
    alert('Testing panel connection...');
    try {
        const data = await apiRequest(`/api/admin/panels/${id}/test`, { method: 'POST' });
        if (data.ok) {
            const xrayState = (data.status.xray || {}).state || '?';
            alert(`✅ Server Connection Successful!\nCPU: ${data.status.cpu}%\nMemory: ${(data.status.mem*100).toFixed(1)}%\nXray state: ${xrayState}\nInbounds found: ${data.inbounds.length}`);
        } else {
            alert(`🔴 Connection Failed:\n${data.error}`);
        }
    } catch (err) {
        alert(`🔴 Error: ${err.message}`);
    }
}

async function deletePanel(id) {
    if (!confirm('Are you sure you want to delete this server entry? It must have no plans or services assigned.')) return;
    try {
        await apiRequest(`/api/admin/panels/${id}`, { method: 'DELETE' });
        loadPanels();
    } catch (err) {
        alert(err.message);
    }
}

// -------------------------------------------------------------
// Tab 6: Reseller Nodes
// -------------------------------------------------------------
async function loadNodes() {
    try {
        const nodes = await apiRequest('/api/admin/nodes');
        const tbody = document.getElementById('nodes-list');
        tbody.innerHTML = '';
        
        if (nodes.length === 0) {
            tbody.innerHTML = `<tr><td colspan="6" class="text-center text-secondary">No reseller bot nodes added.</td></tr>`;
            return;
        }

        nodes.forEach(n => {
            tbody.innerHTML += `
                <tr>
                    <td><code>#${n.id}</code></td>
                    <td><code>${n.owner_tg_id}</code></td>
                    <td>${n.bot_username ? '@' + n.bot_username : '—'}</td>
                    <td><strong>${n.brand_name}</strong></td>
                    <td><span class="badge ${n.is_active ? 'badge-success' : 'badge-danger'}">${n.is_active ? 'Active' : 'Disabled'}</span></td>
                    <td>
                        <button class="btn btn-secondary btn-sm" onclick="openNodeModal(${n.id})">
                            <i class="fa-solid fa-pen-to-square"></i> Edit
                        </button>
                    </td>
                </tr>
            `;
        });
    } catch (err) {
        console.error('Failed to load reseller nodes:', err);
    }
}

// -------------------------------------------------------------
// Tab 7: Settings
// -------------------------------------------------------------
async function loadSettings() {
    try {
        const settings = await apiRequest('/api/admin/settings');
        
        // Fill form fields
        const fillForm = (formId, data) => {
            const form = document.getElementById(formId);
            for (const key in data) {
                const input = form.elements[key];
                if (input) {
                    if (input.type === 'checkbox') {
                        input.checked = !!data[key];
                    } else {
                        input.value = data[key];
                    }
                }
            }
        };

        fillForm('form-settings', settings);
        fillForm('form-settings-payments', settings);
        fillForm('form-settings-addons', settings);

    } catch (err) {
        console.error('Failed to load settings:', err);
    }
}

async function saveSettings(formId) {
    const form = document.getElementById(formId);
    const formData = {};
    
    // Extract values
    Array.from(form.elements).forEach(input => {
        if (!input.name) return;
        if (input.type === 'checkbox') {
            formData[input.name] = input.checked;
        } else if (input.name === 'admin_ids') {
            formData[input.name] = input.value.split(',').map(i => parseInt(i.trim())).filter(i => !isNaN(i));
        } else {
            formData[input.name] = input.value;
        }
    });

    try {
        await apiRequest('/api/admin/settings', {
            method: 'PUT',
            body: JSON.stringify(formData)
        });
        alert('✅ Settings saved successfully!');
        loadSettings();
    } catch (err) {
        alert(`🔴 Error: ${err.message}`);
    }
}

// -------------------------------------------------------------
// Modals & Submits Logic
// -------------------------------------------------------------

// Open Balance Modal
function openBalanceModal(tgId, fullName) {
    document.getElementById('balance-tg-id').value = tgId;
    document.getElementById('balance-user-name').innerHTML = `User: <b>${fullName}</b> (ID: <code>${tgId}</code>)`;
    document.getElementById('balance-amount').value = '';
    document.getElementById('balance-reason').value = '';
    openModal('modal-balance');
}

// Open Reseller Config Modal
async function openResellerModal(tgId, fullName, isReseller, gbPrice, dayPrice, unlimitedPrice) {
    document.getElementById('reseller-tg-id').value = tgId;
    document.getElementById('reseller-user-name').innerHTML = `User: <b>${fullName}</b>`;
    document.getElementById('reseller-enabled').checked = isReseller;
    document.getElementById('reseller-gb-price').value = gbPrice || 0;
    document.getElementById('reseller-day-price').value = dayPrice || 0;
    document.getElementById('reseller-unlimited-price').value = unlimitedPrice || 0;
    
    const listContainer = document.getElementById('reseller-panel-prices-list');
    listContainer.innerHTML = '<p class="text-secondary small">Loading server prices...</p>';
    
    openModal('modal-reseller');
    
    try {
        const resellerPanels = await apiRequest(`/api/admin/users/${tgId}/reseller-panels`);
        listContainer.innerHTML = '';
        if (resellerPanels.length === 0) {
            listContainer.innerHTML = '<p class="text-secondary small">No servers available for resellers.</p>';
            return;
        }
        resellerPanels.forEach(p => {
            const priceVal = p.gb_price !== null ? p.gb_price : '';
            const unlPriceVal = p.unlimited_price !== null ? p.unlimited_price : '';
            listContainer.innerHTML += `
                <div class="form-row" style="margin-bottom: 12px; gap: 10px; display: flex;">
                    <div class="form-group" style="flex: 1; margin-bottom: 0;">
                        <label style="margin-bottom: 4px; font-size: 12px;">${p.panel_name} Price/GB</label>
                        <input type="number" class="reseller-panel-price-input" data-panel-id="${p.panel_id}" step="any" value="${priceVal}" placeholder="Default global">
                    </div>
                    <div class="form-group" style="flex: 1; margin-bottom: 0;">
                        <label style="margin-bottom: 4px; font-size: 12px;">${p.panel_name} Unlimited Price</label>
                        <input type="number" class="reseller-panel-unlimited-price-input" data-panel-id="${p.panel_id}" step="any" value="${unlPriceVal}" placeholder="Default global">
                    </div>
                </div>
            `;
        });
    } catch (err) {
        listContainer.innerHTML = `<p class="text-danger small">Failed to load server prices: ${err.message}</p>`;
    }
}

// Open Order Receipt Modal
let activeReviewOrderId = null;
function openReceiptModal(orderId) {
    activeReviewOrderId = orderId;
    const img = document.getElementById('receipt-img');
    const container = document.getElementById('receipt-preview-img-container');
    
    // Reset loader/image states
    img.style.display = 'none';
    container.querySelector('.loader').style.display = 'flex';
    
    // Set source to fetch route
    img.src = `/api/admin/orders/${orderId}/receipt?token=${state.sessionToken}`;
    img.onload = () => {
        container.querySelector('.loader').style.display = 'none';
        img.style.display = 'block';
    };
    img.onerror = () => {
        container.querySelector('.loader').innerHTML = '<i class="fa-solid fa-triangle-exclamation text-danger"></i> Failed to download receipt image.';
    };

    openModal('modal-receipt');
}

// Open Panel/Server Modal
async function openPanelModal(panelId = null) {
    const form = document.getElementById('form-panel');
    form.reset();
    document.getElementById('panel-id').value = panelId || '';
    document.getElementById('panel-inbounds-diagnostic').style.display = 'none';
    
    if (panelId) {
        document.getElementById('panel-modal-title').textContent = 'Edit 3X-UI Server';
        const p = state.panels.find(x => x.id === panelId);
        if (p) {
            document.getElementById('panel-name').value = p.name;
            document.getElementById('panel-base-url').value = p.base_url;
            document.getElementById('panel-api-token').placeholder = '•••••••• (Fill only to update)';
            document.getElementById('panel-api-token').required = false;
            document.getElementById('panel-sub-url').value = p.sub_base_url;
            document.getElementById('panel-active').checked = p.is_active;
            document.getElementById('panel-verify-tls').checked = p.verify_tls;
            document.getElementById('panel-trials').checked = p.allow_trials;
            document.getElementById('panel-migrations').checked = p.allow_migrations;
            document.getElementById('panel-resellers').checked = p.allow_resellers;
            document.getElementById('panel-reseller-gb-price').value = p.reseller_gb_price || 0;
            document.getElementById('panel-reseller-unlimited-price').value = p.reseller_unlimited_price || 0;
            document.getElementById('panel-sort').value = p.sort_order;
            
            // Show loaded inbounds selection
            await fetchAndRenderPanelInbounds(panelId, p);
        }
    } else {
        document.getElementById('panel-modal-title').textContent = 'Register 3X-UI Server';
        document.getElementById('panel-api-token').placeholder = '';
        document.getElementById('panel-api-token').required = true;
        document.getElementById('panel-reseller-gb-price').value = 0;
        document.getElementById('panel-reseller-unlimited-price').value = 0;
    }
    openModal('modal-panel');
}

async function fetchAndRenderPanelInbounds(panelId, panelDataObj = null) {
    const trialList = document.getElementById('panel-inbounds-trial-list');
    const migrationList = document.getElementById('panel-inbounds-migration-list');
    const resellerList = document.getElementById('panel-inbounds-reseller-list');
    
    trialList.innerHTML = 'Loading...';
    migrationList.innerHTML = 'Loading...';
    resellerList.innerHTML = 'Loading...';
    document.getElementById('panel-inbounds-diagnostic').style.display = 'block';

    try {
        const res = await apiRequest(`/api/admin/panels/${panelId}/test`, { method: 'POST' });
        if (!res.ok) throw new Error(res.error);
        
        state.inboundsFetched[panelId] = res.inbounds;
        
        const renderListBox = (container, listSelected, storageKey) => {
            container.innerHTML = '';
            if (res.inbounds.length === 0) {
                container.innerHTML = '<span class="text-secondary small">No inbounds</span>';
                return;
            }
            res.inbounds.forEach(opt => {
                const checked = listSelected.includes(opt.id) ? 'checked' : '';
                container.innerHTML += `
                    <label class="inbound-list-item">
                        <input type="checkbox" name="${storageKey}" value="${opt.id}" ${checked}>
                        <span>${opt.remark} (${opt.protocol})</span>
                    </label>
                `;
            });
        };

        const currentTrials = panelDataObj ? panelDataObj.trial_inbound_ids : [];
        const currentMigrations = panelDataObj ? panelDataObj.migration_inbound_ids : [];
        const currentResellers = panelDataObj ? panelDataObj.reseller_inbound_ids : [];

        renderListBox(trialList, currentTrials, 'trial_inbound_ids');
        renderListBox(migrationList, currentMigrations, 'migration_inbound_ids');
        renderListBox(resellerList, currentResellers, 'reseller_inbound_ids');

    } catch (err) {
        trialList.innerHTML = `<span class="text-danger small">Error loading</span>`;
        migrationList.innerHTML = `<span class="text-danger small">Error loading</span>`;
        resellerList.innerHTML = `<span class="text-danger small">Error loading</span>`;
        console.error(err);
    }
}

// Open Plan Modal
async function openPlanModal(planId = null) {
    const form = document.getElementById('form-plan');
    form.reset();
    document.getElementById('plan-id').value = planId || '';
    
    // Fill servers dropdown
    const select = document.getElementById('plan-panel-id');
    select.innerHTML = '<option value="">Select server...</option>';
    state.panels.forEach(p => {
        select.innerHTML += `<option value="${p.id}">${p.name}</option>`;
    });

    const container = document.getElementById('plan-inbounds-container');
    container.innerHTML = '<p class="text-center p-3 text-secondary">Select an assigned server (panel) above first to load inbounds.</p>';

    if (planId) {
        document.getElementById('plan-modal-title').textContent = 'Edit Plan';
        try {
            const plans = await apiRequest('/api/admin/plans');
            const p = plans.find(x => x.id === planId);
            if (p) {
                document.getElementById('plan-title').value = p.title;
                document.getElementById('plan-panel-id').value = p.panel_id || '';
                document.getElementById('plan-description').value = p.description;
                document.getElementById('plan-traffic').value = p.traffic_gb;
                document.getElementById('plan-duration').value = p.duration_days;
                document.getElementById('plan-limit-ip').value = p.limit_ip;
                document.getElementById('plan-price-fiat').value = p.price_fiat;
                document.getElementById('plan-price-stars').value = p.price_stars;
                document.getElementById('plan-price-usd').value = p.price_usd;
                document.getElementById('plan-active').checked = p.is_active;
                document.getElementById('plan-trial').checked = p.is_trial;
                document.getElementById('plan-sort').value = p.sort_order;
                
                if (p.panel_id) {
                    await loadPlanServerInbounds(p.panel_id, p.inbound_ids);
                }
            }
        } catch (err) {
            console.error(err);
        }
    } else {
        document.getElementById('plan-modal-title').textContent = 'Create Plan';
    }
    openModal('modal-plan');
}

async function loadPlanServerInbounds(panelId, selectedInboundIds = []) {
    const container = document.getElementById('plan-inbounds-container');
    container.innerHTML = 'Loading Server Inbound details...';
    
    try {
        const res = await apiRequest(`/api/admin/panels/${panelId}/test`, { method: 'POST' });
        if (!res.ok) throw new Error(res.error);
        
        container.innerHTML = '';
        if (res.inbounds.length === 0) {
            container.innerHTML = '<p class="p-3 text-secondary">No inbounds configured on server.</p>';
            return;
        }
        
        res.inbounds.forEach(opt => {
            const checked = selectedInboundIds.includes(opt.id) ? 'checked' : '';
            container.innerHTML += `
                <label class="inbound-list-item">
                    <input type="checkbox" name="inbound_ids" value="${opt.id}" ${checked}>
                    <span><strong>${opt.remark}</strong> (${opt.protocol} on port ${opt.port})</span>
                </label>
            `;
        });
    } catch (err) {
        container.innerHTML = `<p class="p-3 text-danger">Failed to load server inbounds: ${err.message}</p>`;
    }
}

// Open Reseller Node Modal
async function openNodeModal(nodeId = null) {
    const form = document.getElementById('form-node');
    form.reset();
    document.getElementById('node-id').value = nodeId || '';
    
    if (nodeId) {
        document.getElementById('node-modal-title').textContent = 'Edit Reseller Node';
        document.getElementById('node-token-container').style.display = 'none';
        document.getElementById('node-token').required = false;
        
        try {
            const nodes = await apiRequest('/api/admin/nodes');
            const n = nodes.find(x => x.id === nodeId);
            if (n) {
                document.getElementById('node-owner-id').value = n.owner_tg_id;
                document.getElementById('node-brand').value = n.brand_name;
                document.getElementById('node-support').value = n.support_contact;
                document.getElementById('node-active').checked = n.is_active;
                document.getElementById('node-card-num').value = n.card_number;
                document.getElementById('node-card-holder').value = n.card_holder;
            }
        } catch (err) {
            console.error(err);
        }
    } else {
        document.getElementById('node-modal-title').textContent = 'Add Reseller Bot Node';
        document.getElementById('node-token-container').style.display = 'block';
        document.getElementById('node-token').required = true;
    }
    openModal('modal-node');
}

// Modal Utilities
function openModal(id) {
    document.getElementById(id).style.display = 'flex';
}

function closeModal(id) {
    document.getElementById(id).style.display = 'none';
}

// Setup Event Listeners
function setupEventListeners() {
    
    // Auth login click
    document.getElementById('btn-login').addEventListener('click', async () => {
        const token = document.getElementById('login-token').value.trim();
        if (!token) return;
        try {
            const data = await apiRequest(`/api/admin/auth?token=${token}`);
            if (data.session_token) {
                state.sessionToken = data.session_token;
                localStorage.setItem('session_token', data.session_token);
                showDashboard();
            }
        } catch (err) {
            document.getElementById('login-error').textContent = err.message;
        }
    });

    // Logout click
    document.getElementById('btn-logout').addEventListener('click', () => {
        showLoginOverlay();
    });

    // Navigation items click
    document.querySelectorAll('.nav-item').forEach(item => {
        item.addEventListener('click', () => {
            navigateToTab(item.getAttribute('data-tab'));
        });
    });

    // User Search click & keypress
    document.getElementById('btn-search-users').addEventListener('click', () => {
        state.users.search = document.getElementById('users-search').value.trim();
        state.users.offset = 0;
        loadUsers();
    });
    
    document.getElementById('users-search').addEventListener('keypress', (e) => {
        if (e.key === 'Enter') {
            state.users.search = document.getElementById('users-search').value.trim();
            state.users.offset = 0;
            loadUsers();
        }
    });

    // Users Pagination
    document.getElementById('btn-users-prev').addEventListener('click', () => {
        if (state.users.offset >= state.users.limit) {
            state.users.offset -= state.users.limit;
            loadUsers();
        }
    });

    document.getElementById('btn-users-next').addEventListener('click', () => {
        if ((state.users.offset + state.users.limit) < state.users.total) {
            state.users.offset += state.users.limit;
            loadUsers();
        }
    });

    // Orders Filter buttons
    document.querySelectorAll('.tab-btn-sub').forEach(btn => {
        btn.addEventListener('click', (e) => {
            document.querySelectorAll('.tab-btn-sub').forEach(b => b.classList.remove('active'));
            btn.classList.add('active');
            state.orders.status = btn.getAttribute('data-order-status');
            loadOrders();
        });
    });

    // Modals close button
    document.querySelectorAll('.close-modal').forEach(span => {
        span.addEventListener('click', () => {
            span.closest('.modal').style.display = 'none';
        });
    });
    
    window.addEventListener('click', (e) => {
        if (e.target.classList.contains('modal')) {
            e.target.style.display = 'none';
        }
    });

    // Balance adjust submit
    document.getElementById('form-adjust-balance').addEventListener('submit', async (e) => {
        e.preventDefault();
        const tgId = document.getElementById('balance-tg-id').value;
        const amount = document.getElementById('balance-amount').value;
        const reason = document.getElementById('balance-reason').value;
        try {
            await apiRequest(`/api/admin/users/${tgId}/adjust-balance`, {
                method: 'POST',
                body: JSON.stringify({ amount, reason })
            });
            closeModal('modal-balance');
            loadUsers();
        } catch (err) {
            alert(err.message);
        }
    });

    // Reseller wholesale submit
    document.getElementById('form-config-reseller').addEventListener('submit', async (e) => {
        e.preventDefault();
        const tgId = document.getElementById('reseller-tg-id').value;
        const isReseller = document.getElementById('reseller-enabled').checked;
        const reseller_gb_price = document.getElementById('reseller-gb-price').value;
        const reseller_day_price = document.getElementById('reseller-day-price').value;
        const reseller_unlimited_price = document.getElementById('reseller-unlimited-price').value;
        
        const panel_gb_prices = {};
        Array.from(document.querySelectorAll('.reseller-panel-price-input')).forEach(input => {
            const panelId = input.getAttribute('data-panel-id');
            const val = input.value.trim();
            panel_gb_prices[panelId] = val !== '' ? parseFloat(val) : null;
        });

        const panel_unlimited_prices = {};
        Array.from(document.querySelectorAll('.reseller-panel-unlimited-price-input')).forEach(input => {
            const panelId = input.getAttribute('data-panel-id');
            const val = input.value.trim();
            panel_unlimited_prices[panelId] = val !== '' ? parseFloat(val) : null;
        });

        try {
            await apiRequest(`/api/admin/users/${tgId}/toggle-reseller`, {
                method: 'POST',
                body: JSON.stringify({ 
                    is_reseller: isReseller, 
                    reseller_gb_price, 
                    reseller_day_price,
                    reseller_unlimited_price,
                    panel_gb_prices,
                    panel_unlimited_prices
                })
            });
            closeModal('modal-reseller');
            loadUsers();
        } catch (err) {
            alert(err.message);
        }
    });

    // Verify Orders Buttons Actions
    document.getElementById('btn-approve-receipt').addEventListener('click', async () => {
        if (!activeReviewOrderId) return;
        try {
            await apiRequest(`/api/admin/orders/${activeReviewOrderId}/approve`, { method: 'POST' });
            closeModal('modal-receipt');
            loadOrders();
        } catch (err) {
            alert(`Approval failed: ${err.message}`);
        }
    });

    document.getElementById('btn-reject-receipt').addEventListener('click', async () => {
        if (!activeReviewOrderId) return;
        if (!confirm('Are you sure you want to reject this receipt? The user will be notified in bot.')) return;
        try {
            await apiRequest(`/api/admin/orders/${activeReviewOrderId}/reject`, { method: 'POST' });
            closeModal('modal-receipt');
            loadOrders();
        } catch (err) {
            alert(`Rejection failed: ${err.message}`);
        }
    });

    // Save configurations callbacks
    document.getElementById('form-settings').addEventListener('submit', (e) => {
        e.preventDefault();
        saveSettings('form-settings');
    });

    document.getElementById('form-settings-payments').addEventListener('submit', (e) => {
        e.preventDefault();
        saveSettings('form-settings-payments');
    });

    document.getElementById('form-settings-addons').addEventListener('submit', (e) => {
        e.preventDefault();
        saveSettings('form-settings-addons');
    });

    // Save Reseller Node
    document.getElementById('form-node').addEventListener('submit', async (e) => {
        e.preventDefault();
        const id = document.getElementById('node-id').value;
        
        const payload = {
            owner_tg_id: parseInt(document.getElementById('node-owner-id').value),
            brand_name: document.getElementById('node-brand').value,
            support_contact: document.getElementById('node-support').value,
            is_active: document.getElementById('node-active').checked,
            card_number: document.getElementById('node-card-num').value,
            card_holder: document.getElementById('node-card-holder').value
        };

        if (!id) {
            payload.bot_token = document.getElementById('node-token').value;
        }

        try {
            const method = id ? 'PUT' : 'POST';
            const url = id ? `/api/admin/nodes/${id}` : '/api/admin/nodes';
            await apiRequest(url, {
                method,
                body: JSON.stringify(payload)
            });
            closeModal('modal-node');
            loadNodes();
        } catch (err) {
            alert(err.message);
        }
    });

    // Panel Register button action
    document.getElementById('btn-new-panel').addEventListener('click', () => {
        openPanelModal();
    });

    // Panel modal Test & Diagnostics
    document.getElementById('btn-test-panel').addEventListener('click', async () => {
        const panelId = document.getElementById('panel-id').value;
        if (!panelId) {
            alert('Diagnostics can only be run on saved servers. Please save first.');
            return;
        }
        await fetchAndRenderPanelInbounds(panelId);
    });

    // Save server submit
    document.getElementById('form-panel').addEventListener('submit', async (e) => {
        e.preventDefault();
        const id = document.getElementById('panel-id').value;
        
        // Extract checkboxes in the list
        const trial_inbound_ids = Array.from(document.querySelectorAll('#panel-inbounds-trial-list input[type="checkbox"]:checked')).map(i => parseInt(i.value));
        const migration_inbound_ids = Array.from(document.querySelectorAll('#panel-inbounds-migration-list input[type="checkbox"]:checked')).map(i => parseInt(i.value));
        const reseller_inbound_ids = Array.from(document.querySelectorAll('#panel-inbounds-reseller-list input[type="checkbox"]:checked')).map(i => parseInt(i.value));

        const payload = {
            name: document.getElementById('panel-name').value,
            base_url: document.getElementById('panel-base-url').value,
            sub_base_url: document.getElementById('panel-sub-url').value,
            is_active: document.getElementById('panel-active').checked,
            verify_tls: document.getElementById('panel-verify-tls').checked,
            allow_trials: document.getElementById('panel-trials').checked,
            allow_migrations: document.getElementById('panel-migrations').checked,
            allow_resellers: document.getElementById('panel-resellers').checked,
            reseller_gb_price: parseFloat(document.getElementById('panel-reseller-gb-price').value || 0),
            reseller_unlimited_price: parseFloat(document.getElementById('panel-reseller-unlimited-price').value || 0),
            sort_order: parseInt(document.getElementById('panel-sort').value || 0),
            trial_inbound_ids,
            migration_inbound_ids,
            reseller_inbound_ids
        };

        const token = document.getElementById('panel-api-token').value;
        if (token) {
            payload.api_token = token;
        }

        try {
            const method = id ? 'PUT' : 'POST';
            const url = id ? `/api/admin/panels/${id}` : '/api/admin/panels';
            await apiRequest(url, {
                method,
                body: JSON.stringify(payload)
            });
            closeModal('modal-panel');
            loadPanels();
        } catch (err) {
            alert(err.message);
        }
    });

    // Plan server selection dropdown load inbounds
    document.getElementById('plan-panel-id').addEventListener('change', async (e) => {
        const panelId = e.target.value;
        if (panelId) {
            await loadPlanServerInbounds(panelId);
        } else {
            document.getElementById('plan-inbounds-container').innerHTML = '<p class="text-center p-3 text-secondary">Select an assigned server (panel) above first to load inbounds.</p>';
        }
    });

    // Plan Register button action
    document.getElementById('btn-new-plan').addEventListener('click', () => {
        openPlanModal();
    });

    // Save Plan submit
    document.getElementById('form-plan').addEventListener('submit', async (e) => {
        e.preventDefault();
        const id = document.getElementById('plan-id').value;
        
        // Extract selected plan inbounds
        const inbound_ids = Array.from(document.querySelectorAll('#plan-inbounds-container input[type="checkbox"]:checked')).map(i => parseInt(i.value));

        const payload = {
            title: document.getElementById('plan-title').value,
            panel_id: parseInt(document.getElementById('plan-panel-id').value) || null,
            description: document.getElementById('plan-description').value,
            traffic_gb: parseInt(document.getElementById('plan-traffic').value || 0),
            duration_days: parseInt(document.getElementById('plan-duration').value || 0),
            limit_ip: parseInt(document.getElementById('plan-limit-ip').value || 0),
            price_fiat: parseFloat(document.getElementById('plan-price-fiat').value || 0),
            price_stars: parseInt(document.getElementById('plan-price-stars').value || 0),
            price_usd: parseFloat(document.getElementById('plan-price-usd').value || 0),
            is_active: document.getElementById('plan-active').checked,
            is_trial: document.getElementById('plan-trial').checked,
            sort_order: parseInt(document.getElementById('plan-sort').value || 0),
            inbound_ids
        };

        try {
            const method = id ? 'PUT' : 'POST';
            const url = id ? `/api/admin/plans/${id}` : '/api/admin/plans';
            await apiRequest(url, {
                method,
                body: JSON.stringify(payload)
            });
            closeModal('modal-plan');
            loadPlans();
        } catch (err) {
            alert(err.message);
        }
    });
    
    // Register New Reseller Node Bot action
    document.getElementById('btn-new-node').addEventListener('click', () => {
        openNodeModal();
    });
}

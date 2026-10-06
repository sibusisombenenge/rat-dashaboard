// RAT Dashboard - Repository Dashboard JavaScript

let allMetrics = [];
let currentView = 'all';

// Tab switching for metric views
document.querySelectorAll('.tab-btn[data-metric]').forEach(btn => {
    btn.addEventListener('click', () => {
        document.querySelectorAll('.tab-btn[data-metric]').forEach(b => b.classList.remove('active'));
        btn.classList.add('active');
        currentView = btn.dataset.metric;
        loadMetrics();
    });
});

// Load metrics
async function loadMetrics() {
    const loading = document.getElementById('metrics-loading');
    const tbody = document.getElementById('metrics-tbody');
    const summary = document.getElementById('metrics-summary');
    
    loading.style.display = 'block';
    tbody.innerHTML = '';
    summary.innerHTML = '';
    document.getElementById('table-meta').innerHTML = '';
    
    const params = new URLSearchParams();
    const typeFilter = document.getElementById('filter-type').value;
    const authorFilter = document.getElementById('filter-author').value;
    const pathFilter = document.getElementById('filter-path').value;
    const timeFrom = document.getElementById('filter-time-from').value;
    const timeTo = document.getElementById('filter-time-to').value;
    const commitList = document.getElementById('commit-list').value.trim();
    
    if (typeFilter) params.set('object_type', typeFilter);
    if (authorFilter) params.set('author', authorFilter);
    if (pathFilter) params.set('path', pathFilter);
    if (timeFrom) params.set('time_from', Math.floor(new Date(timeFrom).getTime() / 1000));
    if (timeTo) params.set('time_to', Math.floor(new Date(timeTo).getTime() / 1000));
    if (commitList) {
        const hashes = commitList.split('\n').map(s => s.trim()).filter(Boolean);
        params.set('commits', hashes.join(','));
    }
    
    // Apply view filter
    if (currentView === 'repository') params.set('object_type', 'repository');
    else if (currentView === 'directory') params.set('object_type', 'directory');
    else if (currentView === 'file') params.set('object_type', 'file');
    
    try {
        const resp = await fetch(`/api/repos/${REPO_NAME}/metrics?${params}`);
        const data = await resp.json();
        allMetrics = data.metrics || [];
        
        // Compute summary
        const totalAdded = allMetrics.filter(m => m.author_key === 'ALL' && m.object_type === 'repository')
            .reduce((s, m) => s + m.added, 0);
        const totalRemoved = allMetrics.filter(m => m.author_key === 'ALL' && m.object_type === 'repository')
            .reduce((s, m) => s + m.removed, 0);
        const totalChurn = allMetrics.filter(m => m.author_key === 'ALL' && m.object_type === 'repository')
            .reduce((s, m) => s + m.churn, 0);
        const totalMods = allMetrics.filter(m => m.author_key === 'ALL' && m.object_type === 'repository')
            .reduce((s, m) => s + m.modifications, 0);
        
        summary.innerHTML = `
            <div class="summary-card"><div class="value">${totalAdded.toLocaleString()}</div><div class="label">Lines Added</div></div>
            <div class="summary-card"><div class="value">${totalRemoved.toLocaleString()}</div><div class="label">Lines Removed</div></div>
            <div class="summary-card"><div class="value">${(totalAdded - totalRemoved).toLocaleString()}</div><div class="label">Growth</div></div>
            <div class="summary-card"><div class="value">${totalChurn.toLocaleString()}</div><div class="label">Churn</div></div>
            <div class="summary-card"><div class="value">${totalMods.toLocaleString()}</div><div class="label">Modifications</div></div>
            <div class="summary-card"><div class="value">${data.commit_count.toLocaleString()}</div><div class="label">Commits</div></div>
        `;
        
        // Render table
        const sorted = [...allMetrics].sort((a, b) => {
            if (a.object_type !== b.object_type) return a.object_type.localeCompare(b.object_type);
            if (a.object_path !== b.object_path) return a.object_path.localeCompare(b.object_path);
            if (a.author_key === 'ALL') return -1;
            if (b.author_key === 'ALL') return 1;
            return b.churn - a.churn;
        });
        
        for (const m of sorted) {
            const tr = document.createElement('tr');
            const typeLabel = m.object_type === 'repository' ? '📦 Repo' : 
                            m.object_type === 'directory' ? '📁 Dir' : '📄 File';
            const pathDisplay = m.object_type === 'repository' ? '/' : 
                              m.object_type === 'directory' ? (m.object_path || '/') + '/' : m.object_path;
            
            tr.innerHTML = `
                <td>${typeLabel}</td>
                <td title="${m.object_path}">${truncate(pathDisplay, 40)}</td>
                <td title="${m.author_key}">${truncate(m.author_key, 30)}</td>
                <td class="num">${m.added.toLocaleString()}</td>
                <td class="num">${m.removed.toLocaleString()}</td>
                <td class="num">${m.growth.toLocaleString()}</td>
                <td class="num">${m.churn.toLocaleString()}</td>
                <td class="num">${m.modifications.toLocaleString()}</td>
                <td class="num">${formatNum(m.modification_frequency)}</td>
                <td class="num">${formatNum(m.churn_rate)}</td>
                <td class="num">${formatNum(m.ownership)}</td>
            `;
            tbody.appendChild(tr);
        }

        // Row-count indicator + scroll hint (table is a contained scroll view)
        const tableMeta = document.getElementById('table-meta');
        const tableContainer = document.querySelector('.table-container');
        const rowCount = sorted.length;
        const metaLeft = `<span>Showing <strong>${rowCount.toLocaleString()}</strong> row${rowCount !== 1 ? 's' : ''}</span>`;
        tableMeta.innerHTML = metaLeft;
        requestAnimationFrame(() => {
            if (tableContainer && tableContainer.scrollHeight > tableContainer.clientHeight + 1) {
                tableMeta.innerHTML = metaLeft + `<span class="scroll-hint">Scroll to view all rows &darr;</span>`;
            }
        });
    } catch (err) {
        tbody.innerHTML = `<tr><td colspan="11">Error loading metrics: ${err.message}</td></tr>`;
    } finally {
        loading.style.display = 'none';
    }
}

// Apply filters
function applyFilters() {
    loadMetrics();
}

// Clear filters
function clearFilters() {
    document.getElementById('filter-type').value = '';
    document.getElementById('filter-author').value = '';
    document.getElementById('filter-path').value = '';
    document.getElementById('filter-time-from').value = '';
    document.getElementById('filter-time-to').value = '';
    document.getElementById('commit-list').value = '';
    loadMetrics();
}

// Author merge form
document.getElementById('merge-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    const source = document.getElementById('merge-source').value;
    const target = document.getElementById('merge-target').value;
    
    if (!source || !target) return;
    
    const sourceParts = source.match(/^(.+)<(.+)>$/);
    const targetParts = target.match(/^(.+)<(.+)>$/);
    
    if (!sourceParts || !targetParts) {
        alert('Invalid author format');
        return;
    }
    
    const formData = new FormData();
    formData.append('source_name', sourceParts[1].trim());
    formData.append('source_email', sourceParts[2].trim());
    formData.append('target_name', targetParts[1].trim());
    formData.append('target_email', targetParts[2].trim());
    
    try {
        const resp = await fetch(`/api/repos/${REPO_NAME}/authors/merge`, {
            method: 'POST', body: formData
        });
        if (resp.ok) {
            alert('Authors merged successfully!');
            location.reload();
        } else {
            const data = await resp.json();
            alert('Error: ' + data.detail);
        }
    } catch (err) {
        alert('Error: ' + err.message);
    }
});

// Helpers
function truncate(str, max) {
    return str.length > max ? str.substring(0, max) + '...' : str;
}

function formatNum(n) {
    if (n === '' || n === null || n === undefined) return '-';
    if (typeof n === 'number') {
        if (n === 0) return '0';
        return n.toFixed(4);
    }
    return n;
}

// ─── Charts ─────────────────────────────────────────────────────────────────
let churnChart = null, ownershipChart = null, timelineChart = null;

async function renderCharts() {
    // Fetch metrics for charts
    const resp = await fetch(`/api/repos/${REPO_NAME}/metrics`);
    const data = await resp.json();
    const metrics = data.metrics || [];
    
    // Chart 1: Top objects by churn (ALL rows only, exclude repository)
    const objects = metrics
        .filter(m => m.author_key === 'ALL' && m.object_type !== 'repository')
        .sort((a, b) => b.churn - a.churn)
        .slice(0, 10);
    
    const churnCtx = document.getElementById('churn-chart');
    if (churnChart) churnChart.destroy();
    churnChart = new Chart(churnCtx, {
        type: 'bar',
        data: {
            labels: objects.map(o => truncate(o.object_path, 25)),
            datasets: [{
                label: 'Churn',
                data: objects.map(o => o.churn),
                backgroundColor: 'rgba(37, 99, 235, 0.6)',
                borderColor: 'rgba(37, 99, 235, 1)',
                borderWidth: 1
            }]
        },
        options: {
            indexAxis: 'y',
            responsive: true,
            maintainAspectRatio: false,
            plugins: { legend: { display: false } }
        }
    });
    
    // Chart 2: Author ownership (repository level, per-author)
    const authors = metrics
        .filter(m => m.author_key !== 'ALL' && m.object_type === 'repository')
        .sort((a, b) => b.churn - a.churn)
        .slice(0, 8);
    
    const colors = ['#2563eb','#16a34a','#dc2626','#f59e0b','#8b5cf6','#ec4899','#14b8a6','#f97316'];
    const ownCtx = document.getElementById('ownership-chart');
    if (ownershipChart) ownershipChart.destroy();
    ownershipChart = new Chart(ownCtx, {
        type: 'doughnut',
        data: {
            labels: authors.map(a => truncate(a.author_key, 25)),
            datasets: [{
                data: authors.map(a => a.churn),
                backgroundColor: colors,
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: { legend: { position: 'right', labels: { boxWidth: 12 } } }
        }
    });
    
    // Chart 3: Churn over time
    const tlResp = await fetch(`/api/repos/${REPO_NAME}/timeline`);
    const tlData = await tlResp.json();
    const timeline = tlData.timeline || [];
    
    const tlCtx = document.getElementById('timeline-chart');
    if (timelineChart) timelineChart.destroy();
    timelineChart = new Chart(tlCtx, {
        type: 'line',
        data: {
            labels: timeline.map(t => new Date(t.timestamp * 1000).toLocaleDateString()),
            datasets: [{
                label: 'Churn',
                data: timeline.map(t => t.churn),
                borderColor: '#16a34a',
                backgroundColor: 'rgba(22, 163, 74, 0.1)',
                fill: true,
                tension: 0.3,
                pointRadius: 0
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: { legend: { display: false } },
            scales: { x: { ticks: { maxTicksLimit: 8 } } }
        }
    });
}

// Initial load
loadMetrics();
renderCharts();

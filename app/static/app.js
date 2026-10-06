// RAT Dashboard - Index Page JavaScript

// Tab switching
document.querySelectorAll('.tab-btn[data-tab]').forEach(btn => {
    btn.addEventListener('click', () => {
        document.querySelectorAll('.tab-btn[data-tab]').forEach(b => b.classList.remove('active'));
        document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
        btn.classList.add('active');
        document.getElementById(btn.dataset.tab + '-tab').classList.add('active');
    });
});

// Upload form
document.getElementById('upload-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    const status = document.getElementById('upload-status');
    const formData = new FormData();
    formData.append('repo_name', document.getElementById('repo-name-upload').value);
    formData.append('file', document.getElementById('zip-file').files[0]);
    
    status.className = 'status-message';
    status.textContent = 'Uploading and analyzing...';
    status.style.display = 'block';
    
    try {
        const resp = await fetch('/api/repos/upload', { method: 'POST', body: formData });
        const data = await resp.json();
        
        if (resp.ok) {
            status.className = 'status-message success';
            status.textContent = `Success! "${data.repo_name}" analyzed with ${data.commit_count} commits. Reloading...`;
            setTimeout(() => location.reload(), 1500);
        } else {
            status.className = 'status-message error';
            status.textContent = `Error: ${data.detail}`;
        }
    } catch (err) {
        status.className = 'status-message error';
        status.textContent = `Error: ${err.message}`;
    }
});

// Clone form
document.getElementById('clone-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    const status = document.getElementById('upload-status');
    const formData = new FormData();
    formData.append('repo_name', document.getElementById('repo-name-clone').value);
    formData.append('url', document.getElementById('repo-url').value);
    
    status.className = 'status-message';
    status.textContent = 'Cloning and analyzing (this may take a while)...';
    status.style.display = 'block';
    
    try {
        const resp = await fetch('/api/repos/clone', { method: 'POST', body: formData });
        const data = await resp.json();
        
        if (resp.ok) {
            status.className = 'status-message success';
            status.textContent = `Success! "${data.repo_name}" cloned with ${data.commit_count} commits. Reloading...`;
            setTimeout(() => location.reload(), 1500);
        } else {
            status.className = 'status-message error';
            status.textContent = `Error: ${data.detail}`;
        }
    } catch (err) {
        status.className = 'status-message error';
        status.textContent = `Error: ${err.message}`;
    }
});

// Delete repo
async function deleteRepo(name) {
    if (!confirm(`Delete repository "${name}"?`)) return;
    try {
        await fetch(`/api/repos/${name}`, { method: 'DELETE' });
        location.reload();
    } catch (err) {
        alert('Error deleting repository');
    }
}

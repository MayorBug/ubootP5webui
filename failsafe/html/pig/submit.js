function calcSHA256(input) {
	var display = document.getElementById('sha256-display');
	document.querySelectorAll('.file-tip').forEach(function(t){t.style.display='none';});
	display.style.display='';
	var file = input.files[0];
	var info=uiText("分区：")+(window.bkType||'')+uiText("<br>大小：")+formatSize(file.size)+uiText("<br>校验中...");
	display.innerHTML = info;
	var reader = new FileReader();
	reader.onload = function () {
		display.innerHTML = info.replace(uiText("校验中..."),'SHA256: '+sha256(reader.result));
	};
	reader.readAsArrayBuffer(file);
}

function getFlashBackupPref() {
	try {
		return localStorage.getItem('flash_backup') === '1' ? '1' : '';
	} catch (e) {
		return '';
	}
}

function getAutoRebootPref() {
	try {
		return localStorage.getItem('auto_reboot') !== '0' ? '1' : '0';
	} catch (e) {
		return '1';
	}
}

function handleSubmit(e) {
	e.preventDefault();
	setUiBusy(true);
	var form = e.target;
	var btn = form.querySelector('button[type=submit]');
	btn.disabled = true;
	btn.textContent = uiText("上传中...");

	var fbInput = document.getElementById('flash-backup-input');
	if (fbInput) {
		if (getFlashBackupPref() === '1') {
			fbInput.value = '1';
			fbInput.disabled = false;
		} else {
			fbInput.disabled = true;
		}
	}

	var arInput = document.getElementById('auto-reboot-input');
	if (arInput) {
		arInput.value = getAutoRebootPref();
		arInput.disabled = false;
	}

	fetch('/', {
		method: 'POST',
		body: new FormData(form)
	}).then(function(resp) {
		if (!resp.ok) return showFail();
		showStep(1, uiText("校验中..."));
		pollUpgradeStatus();
	}).catch(function() { showFail(); });
}

function showFail(isTypeMismatch) {
	setUiBusy(false);
	document.querySelector('.card').innerHTML=uiText("<h2>验证失败</h2><div class=\"error\"><p>")+(isTypeMismatch?uiText("类型不匹配"):uiText("大小不匹配"))+uiText("</p></div><button onclick=\"window.open('term.html','_blank')\">终端详情</button>");
}
function pollUpgradeStatus() {
	var done = 0;
	function check() {
		if (done) return;
		fetch('/upgrade_status').then(function(r) { return r.text(); }).then(function(s) {
			if (s === 'type_mismatch') { done = 1; return showFail(true); }
			if (s === 'rebooting') { done = 1; setUiBusy(false); return pingDevice(); }
			if (s === 'upgrade_done') {
				setUiBusy(false);
				done = 1;
				var el = document.querySelector('.card');
				if (!el) { el = document.createElement('div'); el.className = 'card'; (document.querySelector('main') || document.body).appendChild(el); }
				el.innerHTML = '<h2>' + uiText('更新完成') + '</h2><p>' + uiText('按需自动或手动重启。') + '</p>';
				var reboot = document.createElement('button');
				reboot.textContent = uiText('手动重启');
				reboot.onclick = function() {
					if (!confirm(uiText('确认重启？'))) return;
					fetch('/webterm/cmd', {method:'POST', headers:{'Content-Type':'text/plain'}, body:'reset'});
					showStep(3, uiText('设备重启中...'), 3);
					pingDevice(3);
				};
				el.appendChild(reboot);
				return;
			}
			if (s === 'flashing') { showStep(2, uiText("写入中...")); return setTimeout(check, 1000); }
			showStep(1, uiText("校验中..."));
			setTimeout(check, 500);
		}).catch(function() { setTimeout(check, 2000); });
	}
	showStep(1, uiText("校验中..."));
	setTimeout(check, 500);
}

document.querySelector('form').addEventListener('submit', handleSubmit);
document.querySelector('input[type=file]').addEventListener('change', function () {
	calcSHA256(this);
});

function checkReboot(){if(location.hash==='#reboot'){showStep(3,uiText("设备重启中..."),3);pingDevice(3);}}
checkReboot();
window.addEventListener('hashchange',checkReboot);
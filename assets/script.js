// =========================================================================
// UX plumbing: toasts, confirm dialogs, HTML escaping, button loading.
// Shared by every user action so nothing fails silently.
// =========================================================================

/** Escape a string for safe insertion into HTML. */
function escapeHtml(value) {
    if (value === null || value === undefined) return '';
    return String(value)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
}

/** Show a transient toast. type: success | error | warning | info. */
function showToast(message, type, opts) {
    opts = opts || {};
    var container = document.getElementById('toast-container');
    if (!container) { console[(type === 'error') ? 'error' : 'log'](message); return; }
    var icons = { success: '✅', error: '⚠️', warning: '⚠️', info: 'ℹ️' };
    type = type || 'info';
    var toast = document.createElement('div');
    toast.className = 'toast toast-' + type;
    toast.setAttribute('role', type === 'error' ? 'alert' : 'status');

    var icon = document.createElement('span');
    icon.className = 'toast-icon';
    icon.textContent = icons[type] || icons.info;

    var body = document.createElement('div');
    body.style.flex = '1';
    // message may contain \n — render as text with line breaks
    String(message).split('\n').forEach(function (line, i) {
        if (i > 0) body.appendChild(document.createElement('br'));
        body.appendChild(document.createTextNode(line));
    });

    var close = document.createElement('button');
    close.className = 'toast-close';
    close.setAttribute('aria-label', 'Dismiss');
    close.textContent = '✕';

    var removed = false;
    function remove() {
        if (removed) return; removed = true;
        toast.classList.add('is-hiding');
        setTimeout(function () { toast.remove(); }, 220);
    }
    close.onclick = remove;

    toast.appendChild(icon);
    toast.appendChild(body);
    toast.appendChild(close);
    container.appendChild(toast);

    var ttl = opts.duration != null ? opts.duration : (type === 'error' ? 7000 : 4000);
    if (ttl > 0) setTimeout(remove, ttl);
    return toast;
}

/** Promise-based confirm dialog (replaces window.confirm). Resolves true/false. */
function confirmDialog(opts) {
    opts = (typeof opts === 'string') ? { message: opts } : (opts || {});
    return new Promise(function (resolve) {
        var host = document.getElementById('dialog-host') || document.body;
        var backdrop = document.createElement('div');
        backdrop.className = 'modal-backdrop';
        var danger = !!opts.danger;
        var card = document.createElement('div');
        card.className = 'modal-card';

        var h = document.createElement('h3');
        h.textContent = opts.title || 'Please confirm';
        var body = document.createElement('div');
        body.className = 'modal-body';
        body.textContent = opts.message || 'Are you sure?';

        var actions = document.createElement('div');
        actions.className = 'modal-actions';
        var cancel = document.createElement('button');
        cancel.className = 'btn';
        cancel.textContent = opts.cancelText || 'Cancel';
        var ok = document.createElement('button');
        ok.className = 'btn ' + (danger ? '' : 'btn-primary');
        if (danger) { ok.style.background = 'var(--danger)'; ok.style.borderColor = 'var(--danger)'; ok.style.color = '#fff'; }
        ok.textContent = opts.confirmText || 'Confirm';

        function done(val) { backdrop.remove(); document.removeEventListener('keydown', onKey); resolve(val); }
        function onKey(e) { if (e.key === 'Escape') done(false); }
        cancel.onclick = function () { done(false); };
        ok.onclick = function () { done(true); };
        backdrop.onclick = function (e) { if (e.target === backdrop) done(false); };
        document.addEventListener('keydown', onKey);

        actions.appendChild(cancel); actions.appendChild(ok);
        card.appendChild(h); card.appendChild(body); card.appendChild(actions);
        backdrop.appendChild(card); host.appendChild(backdrop);
        ok.focus();
    });
}

/** Put a button into a loading state; returns a restore function. */
function buttonLoading(el) {
    var btn = (el && el.jquery) ? el[0] : el;
    if (!btn) return function () {};
    btn.classList.add('is-loading');
    btn.disabled = true;
    return function () { btn.classList.remove('is-loading'); btn.disabled = false; };
}

/** Extract an {error} message from a jqXHR, with a fallback. */
function ajaxError(xhr, fallback) {
    try { return JSON.parse(xhr.responseText).error || fallback; } catch (e) { return fallback; }
}

// https://apexcharts.com/javascript-chart-demos/line-charts/zoomable-timeseries/
var options = {
    series: [],
    chart: {
        type: 'area',
        stacked: false,
        height: 490,
        zoom: {
            type: 'x',
            enabled: true,
            autoScaleYaxis: true
        },
        foreColor: '#e0e0e0'
    },
    dataLabels: {
        enabled: false
    },
    stroke: {
        curve: 'smooth',
    },
    markers: {
        size: 0,
    },
    title: {
        text: 'Channel points (dates are displayed in UTC)',
        align: 'left'
    },
    colors: ["#f9826c"],
    fill: {
        type: 'gradient',
        gradient: {
            shadeIntensity: 1,
            inverseColors: false,
            opacityFrom: 0.5,
            opacityTo: 0,
            stops: [0, 90, 100]
        },
    },
    yaxis: {
        title: {
            text: 'Channel points'
        },
    },
    xaxis: {
        type: 'datetime',
        labels: {
            datetimeUTC: false
        }
    },
    tooltip: {
        theme: 'dark',
        shared: false,
        x: {
            show: true,
            format: 'HH:mm:ss dd MMM',
        },
        custom: ({
            series,
            seriesIndex,
            dataPointIndex,
            w
        }) => {
            return (`<div class="apexcharts-active">
                <div class="apexcharts-tooltip-title">${w.globals.seriesNames[seriesIndex]}</div>
                <div class="apexcharts-tooltip-series-group apexcharts-active" style="order: 1; display: flex; padding-bottom: 0px !important;">
                    <div class="apexcharts-tooltip-text">
                        <div class="apexcharts-tooltip-y-group">
                            <span class="apexcharts-tooltip-text-label"><b>Points</b>: ${series[seriesIndex][dataPointIndex]}</span><br>
                            <span class="apexcharts-tooltip-text-label"><b>Reason</b>: ${w.globals.seriesZ[seriesIndex][dataPointIndex] ? w.globals.seriesZ[seriesIndex][dataPointIndex] : ''}</span>
                        </div>
                    </div>
                </div>
                </div>`)
        }
    },
    noData: {
        text: 'Loading...'
    }
};

var chart = new ApexCharts(document.querySelector("#chart"), options);
var currentStreamer = null;
var annotations = [];

var streamersList = [];
var sortBy = "Name ascending";
var sortField = 'name';

var startDate = new Date();
startDate.setDate(startDate.getDate() - daysAgo);
var endDate = new Date();

$(document).ready(function () {
    var isLogCheckboxChecked = $('#log').prop('checked');
    var autoUpdateLog = true;
    var lastReceivedLogIndex = 0;

    $('#auto-update-log').click(() => {
        autoUpdateLog = !autoUpdateLog;
        $('#auto-update-log').text(autoUpdateLog ? '⏸️' : '▶️');
        if (autoUpdateLog) getLog();
    });

    function getLog() {
        if (isLogCheckboxChecked) {
            $.get(`/log?lastIndex=${lastReceivedLogIndex}`, function (data) {
                $("#log-content").append(document.createTextNode(data));
                $("#log-content").scrollTop($("#log-content")[0].scrollHeight);
                lastReceivedLogIndex += data.length;
                if (autoUpdateLog) setTimeout(getLog, 1000);
            });
        }
    }

    chart.render();

    // Defaults
    if (!localStorage.getItem("annotations")) localStorage.setItem("annotations", true);
    if (!localStorage.getItem("sort-by")) localStorage.setItem("sort-by", "Name ascending");

    $('#annotations').prop("checked", localStorage.getItem("annotations") === "true");
    // Theme was already applied pre-paint (see charts.html head); derive the
    // checkbox from the live data-theme so the toggle and stored theme agree.
    $('#dark-mode').prop("checked",
        (document.documentElement.getAttribute("data-theme") || "dark") !== "light");

    $('#annotations').click(() => {
        localStorage.setItem("annotations", $('#annotations').prop("checked"));
        updateAnnotations();
    });

    $('#dark-mode').click(() => {
        localStorage.setItem("dark-mode", $('#dark-mode').prop("checked"));
        toggleDarkMode();
    });

    $('#startDate').val(formatDate(startDate));
    $('#endDate').val(formatDate(endDate));

    sortBy = localStorage.getItem("sort-by");
    if (sortBy.includes("Points")) sortField = 'points';
    else if (sortBy.includes("Last activity")) sortField = 'last_activity';
    else sortField = 'name';
    $('#sorting-by').html(sortBy + ' <i class="fas fa-angle-down"></i>');
    getStreamers();

    updateAnnotations();
    toggleDarkMode();

    // Log checkbox
    var logCheckboxState = localStorage.getItem('logCheckboxState');
    $('#log').prop('checked', logCheckboxState === 'true');
    if (logCheckboxState === 'true') {
        isLogCheckboxChecked = true;
        $('#auto-update-log').show();
        $('#log-box').show();
        getLog();
    }

    $('#log').change(function () {
        isLogCheckboxChecked = $(this).prop('checked');
        localStorage.setItem('logCheckboxState', isLogCheckboxChecked);
        if (isLogCheckboxChecked) {
            $('#log-box').show();
            $('#auto-update-log').show();
            getLog();
        } else {
            $('#log-box').hide();
            $('#auto-update-log').hide();
        }
    });

    // Dry-run checkbox
    var dryRunState = localStorage.getItem('dryRunState');
    $('#dry-run').prop('checked', dryRunState === 'true');
    if (dryRunState === 'true') $('#dry-run-box').show();

    $('#dry-run').change(function () {
        var isChecked = $(this).prop('checked');
        localStorage.setItem('dryRunState', isChecked);
        if (isChecked) {
            $('#dry-run-box').show();
            if (currentStreamer) loadDryRunData(currentStreamer);
        } else {
            $('#dry-run-box').hide();
        }
    });

    // Sort dropdown toggle
    $('.sort-dropdown-btn').click(function (e) {
        e.stopPropagation();
        $('.sort-dropdown-menu').toggle();
    });
    $(document).click(function () { $('.sort-dropdown-menu').hide(); });

    // Export buttons
    $('#export-csv').click(function () {
        if (!currentStreamer) return;
        var name = currentStreamer.replace('.json', '');
        window.location.href = '/api/export/csv?streamer=' + encodeURIComponent(name);
    });
    $('#export-json').click(function () {
        if (!currentStreamer) return;
        var name = currentStreamer.replace('.json', '');
        window.location.href = '/api/export/json?streamer=' + encodeURIComponent(name);
    });

    // Test Discord notification
    $('#discord-test-btn').click(function () {
        testDiscord(this);
    });

    // Global stats
    loadGlobalStats();
    // Discord connection status badge
    loadDiscordStatus();
});

function testDiscord(btn) {
    var restore = buttonLoading(btn);
    $.ajax({
        url: './api/discord/test',
        method: 'POST',
        success: function (resp) {
            showToast((resp && resp.message) || 'Test notification sent to Discord.', 'success');
        },
        error: function (xhr) {
            showToast(ajaxError(xhr, 'Could not send Discord test'), 'error');
        },
        complete: restore
    });
}

function formatDate(date) {
    var d = new Date(date),
        month = '' + (d.getMonth() + 1),
        day = '' + d.getDate(),
        year = d.getFullYear();

    if (month.length < 2) month = '0' + month;
    if (day.length < 2) day = '0' + day;

    return [year, month, day].join('-');
}

function changeStreamer(streamer, index) {
    // Use ID-based class assignment so group-header <li>s don't skew the index
    $("#streamers-list li").removeClass("active");
    var el = document.getElementById('streamer-' + streamer);
    if (el) el.classList.add('active');
    currentStreamer = streamer;

    options.title.text = `${streamer.replace(".json", "")}'s channel points (dates are displayed in UTC)`;
    chart.updateOptions(options);

    localStorage.setItem("selectedStreamer", currentStreamer);
    getStreamerData(streamer);

    if ($('#dry-run').prop('checked')) {
        loadDryRunData(streamer);
    }

    // Refresh mute panel channel-section if it's open
    if ($('#discord-mute-panel').is(':visible') && _currentMutes !== null) {
        $.ajax({ url: './api/discord/status', method: 'GET', success: function(s) {
            loadChannelMuteForCurrent(s.all_events || []);
        }});
    }
}

function getStreamerData(streamer) {
    if (currentStreamer == streamer) {
        $.getJSON(`./json/${streamer}`, {
            startDate: formatDate(startDate),
            endDate: formatDate(endDate)
        }, function (response) {
            chart.updateSeries([{
                name: streamer.replace(".json", ""),
                data: response["series"]
            }], true)
            clearAnnotations();
            annotations = response["annotations"];
            updateAnnotations();
            setTimeout(function () {
                getStreamerData(streamer);
            }, 300000); // 5 minutes
        });
    }
}

function getAllStreamersData() {
    $.getJSON(`./json_all`, function (response) {
        for (var i in response) {
            chart.appendSeries({
                name: response[i]["name"].replace(".json", ""),
                data: response[i]["data"]["series"]
            }, true)
        }
    });
}

function getStreamers() {
    // Show a skeleton while the first load is in flight
    if (!streamersList || !streamersList.length) {
        var sk = '';
        for (var i = 0; i < 6; i++) sk += '<li class="skeleton skeleton-row" style="list-style:none;"></li>';
        $("#streamers-list").html(sk);
    }

    $.getJSON('streamers', function (response) {
        streamersList = response;
        sortStreamers();

        // Restore the selected streamer from localStorage on page load
        var selectedStreamer = localStorage.getItem("selectedStreamer");

        if (selectedStreamer) {
            currentStreamer = selectedStreamer;
        } else {
            // If no selected streamer is found, default to the first streamer in the list
            currentStreamer = streamersList.length > 0 ? streamersList[0].name : null;
        }

        // Ensure the selected streamer is still active and scrolled into view
        renderStreamers();
    }).fail(function (xhr) {
        $("#streamers-list").html(
            '<li style="list-style:none;"><div class="empty-state">' +
            '<span class="icon">📡</span>' + escapeHtml(ajaxError(xhr, 'Could not load streamers.')) +
            '</div></li>'
        );
    });

    // Auto-refresh streamer list every 10 seconds (live status + points)
    if (!getStreamers._refreshTimer) {
        getStreamers._refreshTimer = setInterval(function () {
            $.getJSON('streamers', function (response) {
                streamersList = response;
                sortStreamers();
                renderStreamers();
            });
        }, 10000);
    }
}

var _openMenu = null; // currently open context-menu element

function renderStreamers() {
    closeStreamerMenu();
    $("#streamers-list").empty();
    var selectedStreamer = localStorage.getItem("selectedStreamer");

    var liveStreamers    = streamersList.filter(function(s) { return s.is_online; });
    var offlineStreamers = streamersList.filter(function(s) { return !s.is_online; });

    function renderGroup(list) {
        list.forEach(function (streamer) {
            var displayname = streamer.name.replace(".json", "");
            var pts = streamer.points;
            var pointsHtml = pts ? '<span class="streamer-points">' + pts.toLocaleString() + '</span>' : '';
            var isActive = currentStreamer === streamer.name;
            if (!isActive && !selectedStreamer && streamersList.indexOf(streamer) === 0) {
                isActive = true;
                currentStreamer = streamer.name;
            }
            var activeClass = isActive ? ' active' : '';
            var onlineClass = streamer.is_online ? ' streamer-live' : '';
            var mutedClass  = (_currentMutes && _currentMutes.muted_channels.indexOf(displayname.toLowerCase()) >= 0) ? ' streamer-muted' : '';
            var dot = streamer.is_online
                ? '<span class="streamer-dot live" title="Live">🟢</span>'
                : '<span class="streamer-dot" title="Offline">⚫</span>';

            // Relative last-activity time
            var relTime = '';
            if (streamer.last_activity) {
                var diffMs = Date.now() - streamer.last_activity;
                var diffH = Math.floor(diffMs / 3600000);
                if (diffH < 1) relTime = '<span class="streamer-rel-time">' + Math.floor(diffMs / 60000) + 'm</span>';
                else if (diffH < 24) relTime = '<span class="streamer-rel-time">' + diffH + 'h</span>';
                else relTime = '<span class="streamer-rel-time">' + Math.floor(diffH / 24) + 'd</span>';
            }

            var li = '<li id="streamer-' + streamer.name + '" class="' + activeClass + onlineClass + mutedClass + '"' +
                ' onclick="changeStreamer(\'' + streamer.name + '\', 0); return false;">' +
                dot +
                '<span class="streamer-name">' + escapeHtml(displayname) + '</span>' +
                '<span class="streamer-info">' + pointsHtml + relTime + '</span>' +
                '<button class="streamer-action-btn" onclick="event.stopPropagation(); showStreamerMenu(\'' + streamer.name + '\', this)" title="Actions">⋯</button>' +
                '</li>';
            $("#streamers-list").append(li);
        });
    }

    if (liveStreamers.length > 0) {
        $("#streamers-list").append('<li class="streamer-group-header">🟢 Live (' + liveStreamers.length + ')</li>');
        renderGroup(liveStreamers);
    }
    if (offlineStreamers.length > 0) {
        $("#streamers-list").append('<li class="streamer-group-header">⚫ Offline (' + offlineStreamers.length + ')</li>');
        renderGroup(offlineStreamers);
    }

    if (!streamersList.length) {
        $("#streamers-list").append(
            '<li style="list-style:none;"><div class="empty-state">' +
            '<span class="icon">📭</span>No streamers tracked yet.<br>Analytics appear once the miner starts watching.' +
            '</div></li>'
        );
    }

    if (currentStreamer) {
        var idx = streamersList.findIndex(function(s) { return s.name === currentStreamer; });
        if (idx >= 0) {
            changeStreamer(currentStreamer, idx);
            var el = document.getElementById('streamer-' + currentStreamer);
            if (el) el.scrollIntoView({ behavior: 'smooth', block: 'center' });
        }
    }
}

// === STREAMER CONTEXT MENU === //

function showStreamerMenu(streamerName, btn) {
    closeStreamerMenu();
    var displayname = streamerName.replace('.json', '');

    var menu = document.createElement('div');
    menu.className = 'streamer-menu-dropdown';
    menu.innerHTML =
        '<div class="streamer-menu-item" onclick="changeStreamer(\'' + streamerName + '\', 0); closeStreamerMenu()">📊 View Chart</div>' +
        '<div class="streamer-menu-item" onclick="sendChannelLogForStreamer(\'' + displayname + '\'); closeStreamerMenu()">📖 Send Discord Log</div>' +
        '<div class="streamer-menu-item" onclick="quickMuteToggle(\'' + displayname + '\'); closeStreamerMenu()">🔕 Toggle Mute</div>' +
        '<div class="streamer-menu-item" onclick="changeStreamer(\'' + streamerName + '\', 0); $(\'#dry-run\').prop(\'checked\', true); $(\'#dry-run-box\').show(); loadDryRunData(\'' + streamerName + '\'); closeStreamerMenu()">🎲 View Strategy</div>';

    // Position near the trigger button
    var rect = btn.getBoundingClientRect();
    menu.style.cssText = 'position:fixed; z-index:3000; top:' + (rect.bottom + 4) + 'px; left:' + Math.max(4, rect.right - 150) + 'px;';
    document.body.appendChild(menu);
    _openMenu = menu;

    // Close on any outside click
    setTimeout(function () {
        $(document).one('click.streamerMenu', function () { closeStreamerMenu(); });
    }, 0);
}

function closeStreamerMenu() {
    if (_openMenu) { _openMenu.remove(); _openMenu = null; }
    $(document).off('click.streamerMenu');
}

function quickMuteToggle(channelName) {
    var lowerName = channelName.toLowerCase();
    if (!_currentMutes) {
        // Load first
        $.getJSON('./api/discord/mutes', function (mutes) {
            _currentMutes = mutes;
            _doQuickMuteToggle(lowerName);
        });
        return;
    }
    _doQuickMuteToggle(lowerName);
}

function _doQuickMuteToggle(lowerName) {
    var idx = _currentMutes.muted_channels.indexOf(lowerName);
    if (idx >= 0) _currentMutes.muted_channels.splice(idx, 1);
    else _currentMutes.muted_channels.push(lowerName);
    saveMuteState(function () { renderStreamers(); });
}

function sendChannelLogForStreamer(channelName) {
    $.ajax({
        url: './api/discord/channel_log?streamer=' + encodeURIComponent(channelName) + '&limit=100',
        method: 'POST',
        success: function () { showToast('Channel log sent to Discord for ' + channelName, 'success'); },
        error: function (xhr) {
            showToast('Channel log failed: ' + ajaxError(xhr, 'unknown error'), 'error');
        }
    });
}

function sortStreamers() {
    streamersList = streamersList.sort((a, b) => {
        return (a[sortField] > b[sortField] ? 1 : -1) * (sortBy.includes("ascending") ? 1 : -1);
    });
}

function changeSortBy(option) {
    sortBy = option.innerText.trim();
    if (sortBy.includes("Points")) sortField = 'points';
    else if (sortBy.includes("Last activity")) sortField = 'last_activity';
    else sortField = 'name';
    sortStreamers();
    renderStreamers();
    $('#sorting-by').html(sortBy + ' <i class="fas fa-angle-down"></i>');
    localStorage.setItem("sort-by", sortBy);
    $('.sort-dropdown-menu').hide();
}

function updateAnnotations() {
    if ($('#annotations').prop("checked") === true) {
        clearAnnotations()
        if (annotations && annotations.length > 0)
            annotations.forEach((annotation, index) => {
                annotations[index]['id'] = `id-${index}`
                chart.addXaxisAnnotation(annotation, true)
            })
    } else clearAnnotations()
}

function clearAnnotations() {
    if (annotations && annotations.length > 0)
        annotations.forEach((annotation, index) => {
            chart.removeAnnotation(annotation['id'])
        })
    chart.clearAnnotations();
}

// Input date
$('#startDate').change(() => {
    startDate = new Date($('#startDate').val());
    getStreamerData(currentStreamer);
});
$('#endDate').change(() => {
    endDate = new Date($('#endDate').val());
    getStreamerData(currentStreamer);
});

// === GLOBAL STATS === //

function loadGlobalStats() {
    $.getJSON('/api/global_stats', function (data) {
        if (data.error) return;
        $('#gs-total-points').text(data.total_current_points != null ? data.total_current_points.toLocaleString() : '—');
        $('#gs-total-gained').text(data.total_points_gained != null ? (data.total_points_gained >= 0 ? '+' : '') + data.total_points_gained.toLocaleString() : '—');
        $('#gs-streamers').text(data.streamer_count || '—');
        $('#gs-win-rate').text(data.overall_win_rate != null ? data.overall_win_rate + '%' : '—');
        $('#gs-best').text(data.most_profitable || '—');
    }).fail(function () {
        // API not available — leave placeholders
    });
    // Refresh every 5 minutes
    setTimeout(loadGlobalStats, 300000);
}

// === DRY RUN STRATEGY COMPARISON === //

function formatPointsPrefix(points) {
    if (points > 0) return '+';
    return '';
}

function loadDryRunData(streamer) {
    var name = streamer.replace(".json", "");
    $.getJSON(`./dry_run_summary/${name}`, function (resp) {
        // New format: {strategies: [...], current_strategy: "..."}
        var summary = resp.strategies || resp;
        var currentStrat = resp.current_strategy || '';
        renderDryRunSummary(summary, currentStrat, name);
    }).fail(function () {
        $('#dry-run-summary').html('<p class="has-text-grey">No dry-run data available yet.</p>');
    });
    $.getJSON(`./dry_run/${name}`, function (history) {
        renderDryRunHistory(history);
    }).fail(function () {
        $('#dry-run-history').html('');
    });
    // Load auto-adjust config
    loadAutoAdjustConfig();
}

function switchStrategy(strategyName, streamerName) {
    var scope = streamerName || (typeof currentStreamer !== 'undefined' ? currentStreamer.replace('.json', '') : '');
    confirmDialog({
        title: 'Switch strategy',
        message: 'Switch strategy to ' + strategyName + ' for ' + (scope || 'all channels') + '?\nThis updates settings.json.',
        confirmText: 'Switch'
    }).then(function (ok) {
        if (!ok) return;
        $.ajax({
            url: './api/strategy/switch',
            method: 'POST',
            contentType: 'application/json',
            data: JSON.stringify({ strategy: strategyName, streamer: scope }),
            success: function (resp) {
                showToast(resp.message || ('Switched to ' + strategyName), 'success');
                // Reload config and refresh dry-run immediately
                $.post('./api/config/reload');
                if (typeof currentStreamer !== 'undefined' && currentStreamer) {
                    loadDryRunData(currentStreamer);
                }
            },
            error: function (xhr) {
                showToast(ajaxError(xhr, 'Strategy switch failed'), 'error');
            }
        });
    });
}

function switchStrategyAll() {
    confirmDialog({
        title: 'Apply best strategy everywhere',
        message: 'Apply the best-performing strategy to ALL channels?\nThis will analyze each channel and set the optimal strategy.',
        confirmText: 'Apply to all'
    }).then(function (ok) {
        if (!ok) return;
        _switchStrategyAllConfirmed();
    });
}

function _switchStrategyAllConfirmed() {
    $.ajax({
        url: './api/strategy/switch_all',
        method: 'POST',
        contentType: 'application/json',
        data: JSON.stringify({}),
        success: function (resp) {
            var count = resp.count || 0;
            var switched = resp.switched || [];

            var html = '<p style="color:var(--text-muted); margin-bottom:0.75rem; font-size:0.85rem;">Switched <strong style="color:var(--text);">' + count + '</strong> channel(s)</p>';
            if (switched.length > 0) {
                html += '<table style="width:100%; border-collapse:collapse; font-size:0.8rem;">';
                html += '<thead><tr style="border-bottom:1px solid var(--border-strong);">' +
                    '<th style="text-align:left; padding:0.3rem 0.5rem; color:var(--text-muted);">Channel</th>' +
                    '<th style="text-align:left; padding:0.3rem 0.5rem; color:var(--text-muted);">Old</th>' +
                    '<th style="text-align:left; padding:0.3rem 0.5rem; color:var(--text-muted);">New</th>' +
                    '</tr></thead><tbody>';
                switched.forEach(function (s) {
                    html += '<tr style="border-bottom:1px solid var(--border);">' +
                        '<td style="padding:0.3rem 0.5rem; color:var(--text);">' + escapeHtml(s.streamer) + '</td>' +
                        '<td style="padding:0.3rem 0.5rem; color:var(--danger);">' + escapeHtml(s.old || '—') + '</td>' +
                        '<td style="padding:0.3rem 0.5rem; color:var(--success);">' + escapeHtml(s.new) + '</td>' +
                        '</tr>';
                });
                html += '</tbody></table>';
            } else {
                html += '<p style="color:var(--text-muted); font-size:0.85rem;">No changes required — all channels already on optimal strategy.</p>';
            }
            $('#strategy-all-modal-content').html(html);
            $('#strategy-all-modal').css('display', 'flex');
            showToast('Applied best strategy to ' + count + ' channel(s)', 'success');

            $.post('./api/config/reload');
            if (typeof currentStreamer !== 'undefined' && currentStreamer) {
                loadDryRunData(currentStreamer);
            }
        },
        error: function (xhr) {
            showToast(ajaxError(xhr, 'Strategy switch failed'), 'error');
        }
    });
}

// Close modal on overlay click
$(document).on('click', '#strategy-all-modal', function (e) {
    if (e.target === this) $(this).hide();
});

function loadAutoAdjustConfig() {
    $.getJSON('./api/strategy/auto_adjust', function (cfg) {
        var container = $('#auto-adjust-controls');
        if (container.length === 0) return;
        container.find('#aa-enabled').prop('checked', cfg.enabled);
        container.find('#aa-threshold').val(cfg.threshold);
        container.find('#aa-min-preds').val(cfg.min_predictions);
    });
}

function saveAutoAdjust() {
    var data = {
        enabled: $('#aa-enabled').is(':checked'),
        threshold: parseInt($('#aa-threshold').val()) || 3,
        min_predictions: parseInt($('#aa-min-preds').val()) || 5,
    };
    $.ajax({
        url: './api/strategy/auto_adjust',
        method: 'POST',
        contentType: 'application/json',
        data: JSON.stringify(data),
        success: function () {
            $('#aa-status').text('Saved!').fadeIn().delay(1500).fadeOut();
            showToast('Auto-adjust settings saved', 'success');
        },
        error: function (xhr) {
            $('#aa-status').text('Failed').fadeIn().delay(1500).fadeOut();
            showToast(ajaxError(xhr, 'Could not save auto-adjust settings'), 'error');
        }
    });
}

function sendDiscordSummary(ev) {
    var restore = buttonLoading(ev && (ev.currentTarget || ev.target));
    $.ajax({
        url: './api/discord/summary',
        method: 'POST',
        success: function (resp) {
            showToast('Sent ' + (resp.embeds || 1) + ' embed(s) for ' + (resp.streamers || 0) + ' streamer(s).', 'success');
        },
        error: function (xhr) {
            showToast(ajaxError(xhr, 'Could not send summary'), 'error');
        },
        complete: restore
    });
}

function sendChannelLog(ev) {
    var name = (typeof currentStreamer !== 'undefined' && currentStreamer)
        ? currentStreamer.replace(".json", "")
        : null;
    if (!name) {
        showToast('Please select a streamer first.', 'warning');
        return;
    }
    var restore = buttonLoading(ev && (ev.currentTarget || ev.target));
    $.ajax({
        url: './api/discord/channel_log?streamer=' + encodeURIComponent(name) + '&limit=100',
        method: 'POST',
        success: function (resp) {
            showToast('Sent ' + (resp.sent || 0) + ' embed(s) covering ' + (resp.total_events || 0) + ' event(s).', 'success');
        },
        error: function (xhr) {
            showToast(ajaxError(xhr, 'Could not send channel log'), 'error');
        },
        complete: restore
    });
}

function runDiscordCleanup(ev) {
    var btn = ev && (ev.currentTarget || ev.target);
    confirmDialog({
        title: 'Clean up & re-post Discord messages',
        message: 'This will:\n1. Fetch old messages\n2. Back up to telemetry DB\n3. Re-post as organized embeds\n4. Delete originals',
        confirmText: 'Run cleanup'
    }).then(function (ok) {
        if (!ok) return;
        var restore = buttonLoading(btn);
        $.ajax({
            url: './api/discord/cleanup?cleanup=true&limit=200',
            method: 'POST',
            success: function (resp) {
                showToast('Cleanup done — ' + (resp.total || 0) + ' found, ' +
                    (resp.backed_up || 0) + ' backed up, ' + (resp.migrated || 0) + ' migrated.', 'success');
            },
            error: function (xhr) {
                showToast(ajaxError(xhr, 'Cleanup failed'), 'error');
            },
            complete: restore
        });
    });
}

function runDiscordPurge(ev) {
    var btn = ev && (ev.currentTarget || ev.target);
    confirmDialog({
        title: '🔥 Purge & rebuild Discord channel',
        danger: true,
        confirmText: 'Purge & rebuild',
        message: 'This will DELETE ALL messages in the Discord channel and rebuild ONE logbook embed per streamer.\n\n' +
            'Requires bot_token + channel_id for a full purge.\nWebhook-only: only stored logbook messages will be cleared.'
    }).then(function (ok) {
        if (!ok) return;
        var restore = buttonLoading(btn);
        $.ajax({
            url: './api/discord/cleanup?purge=true&rebuild=true&limit=500',
            method: 'POST',
            success: function (resp) {
                showToast('Purge complete — deleted ' + (resp.purged || 0) + ' message(s), rebuilt ' +
                    (resp.logbooks_sent || 0) + ' logbook(s).', 'success');
            },
            error: function (xhr) {
                showToast(ajaxError(xhr, 'Purge failed'), 'error');
            },
            complete: restore
        });
    });
}

function importTelemetryDB() {
    $('#db-import-file').click();
}

function handleDBImport(fileInput) {
    var file = fileInput.files[0];
    if (!file) return;
    var formData = new FormData();
    formData.append('file', file);
    $.ajax({
        url: './api/telemetry/import_db',
        method: 'POST',
        data: formData,
        processData: false,
        contentType: false,
        success: function (resp) {
            var imported = resp.imported || {};
            var summary = Object.keys(imported).map(function(k) {
                return k + ': ' + imported[k];
            }).join(', ');
            showToast('Import successful' + (summary ? ' — ' + summary : '') + '.', 'success');
            fileInput.value = '';
        },
        error: function (xhr) {
            showToast(ajaxError(xhr, 'Import failed'), 'error');
            fileInput.value = '';
        }
    });
}


// === DISCORD STATUS + MUTE PANEL === //

var _currentMutes = null; // cache: {muted_channels, muted_events_per_channel, global_muted_events}
var _allDiscordEvents = []; // cache: [{event, icon, category}, ...]

function loadDiscordStatus() {
    $.ajax({
        url: './api/discord/status',
        method: 'GET',
        success: function (status) {
            var badge = $('#discord-status-badge');
            if (status.configured) {
                var parts = [];
                if (status.webhook_set) parts.push('webhook');
                if (status.bot_set) parts.push('bot');
                var tip = 'Discord configured: ' + parts.join(', ');
                badge.html('<span style="color:#36b535; font-size:0.75rem;" title="' + tip + '">✅ Connected</span>');
            } else {
                badge.html('<span style="color:#ff4545; font-size:0.75rem;" title="Discord not configured in settings.json">❌ Not configured</span>');
            }
            // Cache mute state and event list
            _currentMutes = {
                muted_channels: status.muted_channels || [],
                muted_events_per_channel: status.muted_events_per_channel || {},
                global_muted_events: status.global_muted_events || [],
            };
            _allDiscordEvents = status.all_events || [];
            // Re-render sidebar mute classes
            refreshStreamerMuteIcons();
        },
        error: function () {
            $('#discord-status-badge').html('<span style="color:#888; font-size:0.75rem;">⚠️ ?</span>');
        }
    });
}

function toggleMutePanel() {
    var panel = $('#discord-mute-panel');
    if (panel.is(':visible')) {
        panel.slideUp(150);
    } else {
        panel.slideDown(150);
        loadMutePanel();
    }
}

function loadMutePanel() {
    $.ajax({
        url: './api/discord/status',
        method: 'GET',
        success: function (status) {
            _currentMutes = {
                muted_channels: status.muted_channels || [],
                muted_events_per_channel: status.muted_events_per_channel || {},
                global_muted_events: status.global_muted_events || [],
            };
            _allDiscordEvents = status.all_events || [];
            _renderGlobalEventMutes();
            loadChannelMuteForCurrent(_allDiscordEvents);
        },
        error: function () {
            $('#discord-mute-panel').html('<p style="font-size:0.8rem; color:#ff4545;">Failed to load Discord status.</p>');
        }
    });
}

function _renderGlobalEventMutes() {
    var html = '';
    _allDiscordEvents.forEach(function (ev) {
        var isMuted = _currentMutes && _currentMutes.global_muted_events.indexOf(ev.event) >= 0;
        var cls = 'event-chip' + (isMuted ? ' event-chip-muted' : '');
        html += '<button class="' + cls + '" onclick="toggleGlobalMute(\'' + ev.event + '\')" title="Global mute: ' + ev.event + '">' +
            ev.icon + ' ' + ev.event.replace(/_/g, ' ') + '</button>';
    });
    $('#global-event-mutes').html(html || '<span style="font-size:0.75rem; color:#9da2b8;">No events available.</span>');
}

function _renderChannelEventMutes(channelName) {
    var html = '';
    var chanMuted = (_currentMutes && _currentMutes.muted_events_per_channel[channelName]) || [];
    _allDiscordEvents.forEach(function (ev) {
        var isMuted = chanMuted.indexOf(ev.event) >= 0;
        var cls = 'event-chip event-chip-sm' + (isMuted ? ' event-chip-muted' : '');
        html += '<button class="' + cls + '" onclick="toggleChannelEventMute(\'' + channelName + '\', \'' + ev.event + '\')" title="Mute for ' + channelName + ': ' + ev.event + '">' +
            ev.icon + ' ' + ev.event.replace(/_/g, ' ') + '</button>';
    });
    $('#channel-event-mutes').html(html || '<span style="font-size:0.75rem; color:#9da2b8;">No events available.</span>');
}

function loadChannelMuteForCurrent(allEvents) {
    if (allEvents && allEvents.length > 0) _allDiscordEvents = allEvents;
    if (!currentStreamer) {
        $('#channel-mute-name').text('Select a streamer first');
        $('#channel-mute-checkbox').prop('disabled', true).prop('checked', false);
        $('#channel-event-mutes-section').hide();
        return;
    }
    var name = currentStreamer.replace('.json', '').toLowerCase();
    var isMuted = _currentMutes && _currentMutes.muted_channels.indexOf(name) >= 0;
    $('#channel-mute-name').text('Mute entire channel: ' + name);
    $('#channel-mute-checkbox').prop('checked', isMuted).prop('disabled', false);
    $('#channel-event-mutes-chan').text(name);
    $('#channel-event-mutes-section').show();
    _renderChannelEventMutes(name);
}

function toggleChannelMute(checkbox) {
    if (!currentStreamer) return;
    if (!_currentMutes) _currentMutes = { muted_channels: [], muted_events_per_channel: {}, global_muted_events: [] };
    var name = currentStreamer.replace('.json', '').toLowerCase();
    if (checkbox.checked) {
        if (_currentMutes.muted_channels.indexOf(name) < 0) _currentMutes.muted_channels.push(name);
    } else {
        var idx = _currentMutes.muted_channels.indexOf(name);
        if (idx >= 0) _currentMutes.muted_channels.splice(idx, 1);
    }
    saveMuteState(function () { refreshStreamerMuteIcons(); });
}

function toggleGlobalMute(eventName) {
    if (!_currentMutes) _currentMutes = { muted_channels: [], muted_events_per_channel: {}, global_muted_events: [] };
    var idx = _currentMutes.global_muted_events.indexOf(eventName);
    if (idx >= 0) _currentMutes.global_muted_events.splice(idx, 1);
    else _currentMutes.global_muted_events.push(eventName);
    saveMuteState();
    _renderGlobalEventMutes();
}

function toggleChannelEventMute(channelName, eventName) {
    if (!_currentMutes) _currentMutes = { muted_channels: [], muted_events_per_channel: {}, global_muted_events: [] };
    if (!_currentMutes.muted_events_per_channel[channelName]) _currentMutes.muted_events_per_channel[channelName] = [];
    var arr = _currentMutes.muted_events_per_channel[channelName];
    var idx = arr.indexOf(eventName);
    if (idx >= 0) {
        arr.splice(idx, 1);
        if (arr.length === 0) delete _currentMutes.muted_events_per_channel[channelName];
    } else {
        arr.push(eventName);
    }
    saveMuteState();
    _renderChannelEventMutes(channelName);
}

function saveMuteState(callback) {
    if (!_currentMutes) return;
    $.ajax({
        url: './api/discord/mutes',
        method: 'POST',
        contentType: 'application/json',
        data: JSON.stringify(_currentMutes),
        success: function () { if (callback) callback(); },
        error: function () { console.warn('Failed to save mute state to server'); }
    });
}

function refreshStreamerMuteIcons() {
    if (!_currentMutes) return;
    streamersList.forEach(function (s) {
        var name = s.name.replace('.json', '').toLowerCase();
        var li = document.getElementById('streamer-' + s.name);
        if (!li) return;
        var isMuted = _currentMutes.muted_channels.indexOf(name) >= 0;
        $(li).toggleClass('streamer-muted', isMuted);
    });
}


function renderDryRunSummary(summary, currentStrategy, streamerName) {
    if (!summary || summary.length === 0) {
        $('#dry-run-summary').html('<p class="dry-run-placeholder">No dry-run data available yet. Predictions will appear here after events resolve.</p>');
        return;
    }

    var csUpper = (currentStrategy || '').toUpperCase();

    // Find best switchable strategy (skip ACTIVE — it cannot be configured directly)
    var bestNet = 0;
    var bestStrat = '';
    for (var bi = 0; bi < summary.length; bi++) {
        if (summary[bi].strategy !== 'ACTIVE') {
            bestStrat = summary[bi].strategy;
            bestNet = summary[bi].net_points;
            break;
        }
    }

    // Action buttons row
    var actions = '<div style="display:flex; gap:0.5rem; margin-bottom:0.75rem; flex-wrap:wrap; align-items:center;">';
    if (csUpper) {
        actions += '<span style="font-size:0.85rem; color:#9da2b8;">Current: <strong style="color:#f0c040;">' + csUpper + '</strong></span>';
    }
    if (bestStrat && bestStrat.toUpperCase() !== csUpper) {
        actions += '<button class="btn-switch" onclick="switchStrategy(\'' + bestStrat + '\', \'' + (streamerName || '') + '\')">⚡ Use ' + bestStrat + '</button>';
    }
    actions += '<button class="btn-switch-sm" onclick="switchStrategyAll()" title="Apply best strategy to all channels">🌐 Best → All</button>';
    actions += '</div>';

    var html = actions;
    html += '<table class="dry-run-table">';
    html += '<thead><tr>';
    html += '<th>Strategy</th><th>Total</th><th>Wins</th><th>Losses</th>';
    html += '<th>Win Rate</th><th>Net Points</th><th>vs Best</th><th>Status</th>';
    html += '</tr></thead><tbody>';

    summary.forEach(function (s, idx) {
        var isCurrent = csUpper && s.strategy.toUpperCase() === csUpper;
        var rowClass = '';
        if (s.is_best) rowClass = 'dry-run-best';
        else if (isCurrent) rowClass = 'dry-run-active';

        var statusBadges = '';
        if (isCurrent) statusBadges += '<span class="tag-warning">▶ CURRENT</span> ';
        if (s.is_best) statusBadges += '<span class="tag-success">BEST</span> ';

        // Show switch button for non-active strategies
        if (!isCurrent && s.strategy !== 'ACTIVE') {
            statusBadges += '<button class="btn-switch-sm" onclick="switchStrategy(\'' + s.strategy + '\', \'' + (streamerName || '') + '\')" title="Switch to ' + s.strategy + '">↗</button>';
        }
        if (!statusBadges) statusBadges = '-';

        var pointsColor = s.net_points >= 0 ? 'color:#36b535' : 'color:#ff4545';
        var pointsPrefix = formatPointsPrefix(s.net_points);

        // vs best calculation (ACTIVE shows its positive diff; is_best row shows '—')
        var diff = s.net_points - bestNet;
        var diffStr = s.is_best ? '—' : (diff >= 0 ? '+' : '') + diff.toLocaleString();
        var diffColor = s.is_best ? 'color:#9da2b8' : (diff >= 0 ? 'color:#36b535' : 'color:#ff4545');

        html += '<tr class="' + rowClass + '">';
        html += '<td><strong>' + s.strategy + '</strong></td>';
        html += '<td>' + s.total + '</td>';
        html += '<td>' + s.wins + '</td>';
        html += '<td>' + s.losses + '</td>';
        html += '<td>' + (s.win_rate != null ? s.win_rate + '%' : 'N/A') + '</td>';
        html += '<td style="' + pointsColor + '">' + pointsPrefix + s.net_points + '</td>';
        html += '<td style="' + diffColor + '">' + diffStr + '</td>';
        html += '<td>' + statusBadges + '</td>';
        html += '</tr>';
    });

    html += '</tbody></table>';
    $('#dry-run-summary').html(html);
}

function renderDryRunHistory(history) {
    if (!history || history.length === 0) {
        $('#dry-run-history').html('');
        return;
    }

    // Detect format:
    // 1. Grouped multi-strategy: has 'strategies' array + 'timestamp' (from dry_run_results table)
    // 2. Flat SQLite: has 'timestamp' + 'result' but no 'strategies' and no 'x'
    // 3. Legacy JSON: has 'x' + 'strategies'
    var first = history[0];
    var isGrouped = first && first.strategies && first.timestamp && !first.x;
    var isFlatSqlite = first && first.timestamp && !first.x && !first.strategies;

    if (isGrouped) {
        // Multi-strategy format from dry_run_results table
        var recent = history.slice(0, 15);
        var html = '<div class="card-title" style="margin-bottom:0.5rem;">Recent Predictions (All Strategies)</div>';
        html += '<div class="dry-run-history-list">';

        recent.forEach(function (pred) {
            var dateStr = new Date(pred.timestamp).toLocaleString();
            html += '<div class="dry-run-prediction-card">';
            html += '<div class="dry-run-prediction-header">';
            html += '<strong>' + (pred.event_title || 'Prediction') + '</strong>';
            html += '<span class="dry-run-date"> ' + dateStr + '</span>';
            if (pred.active_strategy) {
                html += '<span class="tag-info" style="margin-left:0.5rem; font-size:0.75rem;">Active: ' + pred.active_strategy + '</span>';
            }
            html += '</div>';
            html += '<div class="dry-run-prediction-strategies">';

            if (pred.strategies) {
                pred.strategies.forEach(function (s) {
                    var icon = '', clr = '';
                    if (s.result_type === 'WIN') { icon = '✅'; clr = 'color:#36b535'; }
                    else if (s.result_type === 'LOSE') { icon = '❌'; clr = 'color:#ff4545'; }
                    else if (s.result_type === 'REFUND') { icon = '🔄'; clr = 'color:#9da2b8'; }
                    else { icon = '⏳'; clr = 'color:#9da2b8'; }

                    var isActive = s.strategy === pred.active_strategy;
                    var activeMark = isActive ? ' <span class="tag-warning" style="font-size:0.7rem;">ACTIVE</span>' : '';
                    var prefix = formatPointsPrefix(s.points_gained);

                    html += '<span class="dry-run-strategy-item" style="' + clr + '">';
                    html += icon + ' <strong>' + s.strategy + '</strong>';
                    html += ' → ' + (s.outcome_title || ('Outcome ' + (s.choice != null ? s.choice : '?')));
                    if (s.result_type) html += ' (' + prefix + (s.points_gained || 0) + ')';
                    html += activeMark;
                    html += '</span>';
                });
            }
            html += '</div></div>';
        });

        html += '</div>';
        $('#dry-run-history').html(html);
        return;
    }

    if (isFlatSqlite) {
        // Flat prediction rows from predictions table (no multi-strategy)
        var recent = history.slice(0, 20);
        var html = '<div class="card-title" style="margin-bottom:0.5rem;">Recent Predictions</div>';
        html += '<div class="dry-run-history-list">';

        recent.forEach(function (pred) {
            var dateStr = new Date(pred.timestamp).toLocaleString();
            var icon = '', clr = '';
            if (pred.result === 'WIN') { icon = '✅'; clr = 'color:#36b535'; }
            else if (pred.result === 'LOSE') { icon = '❌'; clr = 'color:#ff4545'; }
            else if (pred.result === 'REFUND') { icon = '🔄'; clr = 'color:#9da2b8'; }
            else { icon = '⏳'; clr = 'color:#9da2b8'; }

            var pointsPrefix = formatPointsPrefix(pred.points_gained || 0);
            var pointsText = pred.result ? ' (' + pointsPrefix + (pred.points_gained || 0) + ')' : '';

            html += '<div class="dry-run-prediction-card">';
            html += '<div class="dry-run-prediction-header">';
            html += '<strong>' + (pred.title || 'Prediction') + '</strong>';
            html += '<span class="dry-run-date"> ' + dateStr + '</span>';
            html += '</div>';
            html += '<div class="dry-run-prediction-strategies">';
            html += '<span class="dry-run-strategy-item" style="' + clr + '">';
            html += icon + ' ' + (pred.choice_title || '?');
            if (pred.choice_color) html += ' <span class="tag-info" style="font-size:0.7rem;">' + pred.choice_color + '</span>';
            html += pointsText;
            html += '</span>';
            html += '</div></div>';
        });

        html += '</div>';
        $('#dry-run-history').html(html);
        return;
    }

    // Legacy JSON format — nested strategies per prediction (x timestamp)
    var recent = history.slice(-10).reverse();
    var html = '<div class="card-title" style="margin-bottom:0.5rem;">Recent Predictions</div>';
    html += '<div class="dry-run-history-list">';

    recent.forEach(function (pred) {
        var date = new Date(pred.x);
        var dateStr = date.toLocaleString();

        html += '<div class="dry-run-prediction-card">';
        html += '<div class="dry-run-prediction-header">';
        html += '<strong>' + (pred.event_title || 'Prediction') + '</strong>';
        html += '<span class="dry-run-date"> ' + dateStr + '</span>';
        html += '<span class="tag-info" style="margin-left:0.5rem; font-size:0.75rem;">Active: ' + (pred.active_strategy || '-') + '</span>';
        html += '</div>';
        html += '<div class="dry-run-prediction-strategies">';

        if (pred.strategies) {
            pred.strategies.forEach(function (s) {
                var icon = '', clr = '';
                if (s.result_type === 'WIN') { icon = '✅'; clr = 'color:#36b535'; }
                else if (s.result_type === 'LOSE') { icon = '❌'; clr = 'color:#ff4545'; }
                else if (s.result_type === 'REFUND') { icon = '🔄'; clr = 'color:#9da2b8'; }
                else { icon = '⏳'; clr = 'color:#9da2b8'; }

                var isActive = s.strategy === pred.active_strategy;
                var activeMark = isActive ? ' <span class="tag-warning" style="font-size:0.7rem;">ACTIVE</span>' : '';
                var prefix = formatPointsPrefix(s.points_gained);

                html += '<span class="dry-run-strategy-item" style="' + clr + '">';
                html += icon + ' <strong>' + s.strategy + '</strong>';
                html += ' → ' + (s.outcome_title || ('Outcome ' + (s.choice || '?')));
                if (s.result_type) html += ' (' + prefix + (s.points_gained || 0) + ')';
                html += activeMark;
                html += '</span>';
            });
        }

        html += '</div></div>';
    });

    html += '</div>';
    $('#dry-run-history').html(html);
}

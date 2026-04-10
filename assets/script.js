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
        // background: '#2B2D3E',
        foreColor: '#fff'
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
    // Variable to keep track of whether log checkbox is checked
    var isLogCheckboxChecked = $('#log').prop('checked');

    // Variable to keep track of whether auto-update log is active
    var autoUpdateLog = true;

    // Variable to keep track of the last received log index
    var lastReceivedLogIndex = 0;

    $('#auto-update-log').click(() => {
        autoUpdateLog = !autoUpdateLog;
        $('#auto-update-log').text(autoUpdateLog ? '⏸️' : '▶️');

        if (autoUpdateLog) {
            getLog();
        }
    });

    // Function to get the full log content
    function getLog() {
        if (isLogCheckboxChecked) {
            $.get(`/log?lastIndex=${lastReceivedLogIndex}`, function (data) {
                // Process and display the new log entries received
                $("#log-content").append(data);
                // Scroll to the bottom of the log content
                $("#log-content").scrollTop($("#log-content")[0].scrollHeight);

                // Update the last received log index
                lastReceivedLogIndex += data.length;

                if (autoUpdateLog) {
                    // Call getLog() again after a certain interval (e.g., 1 second)
                    setTimeout(getLog, 1000);
                }
            });
        }
    }

    // Retrieve the saved header visibility preference from localStorage
    var headerVisibility = localStorage.getItem('headerVisibility');

    // Set the initial header visibility based on the saved preference or default to 'visible'
    if (headerVisibility === 'hidden') {
        $('#toggle-header').prop('checked', false);
        $('#header').hide();
    } else {
        $('#toggle-header').prop('checked', true);
        $('#header').show();
    }

    // Handle the toggle header change event
    $('#toggle-header').change(function () {
        if (this.checked) {
            $('#header').show();
            // Save the header visibility preference as 'visible' in localStorage
            localStorage.setItem('headerVisibility', 'visible');
        } else {
            $('#header').hide();
            // Save the header visibility preference as 'hidden' in localStorage
            localStorage.setItem('headerVisibility', 'hidden');
        }
    });

    chart.render();

    if (!localStorage.getItem("annotations")) localStorage.setItem("annotations", true);
    if (!localStorage.getItem("dark-mode")) localStorage.setItem("dark-mode", true);
    if (!localStorage.getItem("sort-by")) localStorage.setItem("sort-by", "Name ascending");

    // Restore settings from localStorage on page load
    $('#annotations').prop("checked", localStorage.getItem("annotations") === "true");
    $('#dark-mode').prop("checked", localStorage.getItem("dark-mode") === "true");

    // Handle the annotation toggle click event
    $('#annotations').click(() => {
        var isChecked = $('#annotations').prop("checked");
        localStorage.setItem("annotations", isChecked);
        updateAnnotations();
    });

    // Handle the dark mode toggle click event
    $('#dark-mode').click(() => {
        var isChecked = $('#dark-mode').prop("checked");
        localStorage.setItem("dark-mode", isChecked);
        toggleDarkMode();
    });

    $('#startDate').val(formatDate(startDate));
    $('#endDate').val(formatDate(endDate));

    sortBy = localStorage.getItem("sort-by");
    if (sortBy.includes("Points")) sortField = 'points';
    else if (sortBy.includes("Last activity")) sortField = 'last_activity';
    else sortField = 'name';
    $('#sorting-by').text(sortBy);
    getStreamers();

    updateAnnotations();
    toggleDarkMode();

    // Retrieve log checkbox state from localStorage and update UI accordingly
    var logCheckboxState = localStorage.getItem('logCheckboxState');
    $('#log').prop('checked', logCheckboxState === 'true');
    if (logCheckboxState === 'true') {
        isLogCheckboxChecked = true;
        $('#auto-update-log').show();
        $('#log-box').show();
        // Start continuously updating the log content
        getLog();
    }

    // Handle the log checkbox change event
    $('#log').change(function () {
        isLogCheckboxChecked = $(this).prop('checked');
        localStorage.setItem('logCheckboxState', isLogCheckboxChecked);

        if (isLogCheckboxChecked) {
            $('#log-box').show();
            $('#auto-update-log').show();
            getLog();
            $('html, body').scrollTop($(document).height());
        } else {
            $('#log-box').hide();
            $('#auto-update-log').hide();
            // Clear log content when checkbox is unchecked
            // $("#log-content").text('');
        }
    });

    // Dry-run checkbox
    var dryRunState = localStorage.getItem('dryRunState');
    $('#dry-run').prop('checked', dryRunState === 'true');
    if (dryRunState === 'true') {
        $('#dry-run-box').show();
    }

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
});

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
    $("li").removeClass("is-active")
    $("li").eq(index - 1).addClass('is-active');
    currentStreamer = streamer;

    // Update the chart title with the current streamer's name
    options.title.text = `${streamer.replace(".json", "")}'s channel points (dates are displayed in UTC)`;
    chart.updateOptions(options);

    // Save the selected streamer in localStorage
    localStorage.setItem("selectedStreamer", currentStreamer);

    getStreamerData(streamer);

    // Refresh dry-run data if panel is visible
    if ($('#dry-run').prop('checked')) {
        loadDryRunData(streamer);
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
    });
}

function renderStreamers() {
    $("#streamers-list").empty();
    var promised = new Promise((resolve, reject) => {
        streamersList.forEach((streamer, index, array) => {
            displayname = streamer.name.replace(".json", "");
            if (sortField == 'points') displayname = "<font size='-2'>" + streamer['points'] + "</font>&nbsp;" + displayname;
            else if (sortField == 'last_activity') displayname = "<font size='-2'>" + formatDate(streamer['last_activity']) + "</font>&nbsp;" + displayname;
            var isActive = currentStreamer === streamer.name;
            if (!isActive && localStorage.getItem("selectedStreamer") === null && index === 0) {
                isActive = true;
                currentStreamer = streamer.name;
            }
            var activeClass = isActive ? 'is-active' : '';
            var listItem = `<li id="streamer-${streamer.name}" class="${activeClass}"><a onClick="changeStreamer('${streamer.name}', ${index + 1}); return false;">${displayname}</a></li>`;
            $("#streamers-list").append(listItem);
            if (isActive) {
                // Scroll the selected streamer into view
                document.getElementById(`streamer-${streamer.name}`).scrollIntoView({
                    behavior: 'smooth',
                    block: 'center'
                });
            }
            if (index === array.length - 1) resolve();
        });
    });
    promised.then(() => {
        changeStreamer(currentStreamer, streamersList.findIndex(streamer => streamer.name === currentStreamer) + 1);
    });
}

function sortStreamers() {
    streamersList = streamersList.sort((a, b) => {
        return (a[sortField] > b[sortField] ? 1 : -1) * (sortBy.includes("ascending") ? 1 : -1);
    });
}

function changeSortBy(option) {
    sortBy = option.innerText.trim();
    if (sortBy.includes("Points")) sortField = 'points'
    else if (sortBy.includes("Last activity")) sortField = 'last_activity'
    else sortField = 'name';
    sortStreamers();
    renderStreamers();
    $('#sorting-by').text(sortBy);
    localStorage.setItem("sort-by", sortBy);
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

// Toggle
$('#annotations').click(() => {
    updateAnnotations();
});
$('#dark-mode').click(() => {
    toggleDarkMode();
});

$('.dropdown').click(() => {
    $('.dropdown').toggleClass('is-active');
});

// Input date
$('#startDate').change(() => {
    startDate = new Date($('#startDate').val());
    getStreamerData(currentStreamer);
});
$('#endDate').change(() => {
    endDate = new Date($('#endDate').val());
    getStreamerData(currentStreamer);
});

// === DRY RUN STRATEGY COMPARISON === //

function formatPointsPrefix(points) {
    if (points > 0) return '+';
    return '';
}

function loadDryRunData(streamer) {
    var name = streamer.replace(".json", "");
    $.getJSON(`./dry_run_summary/${name}`, function (summary) {
        renderDryRunSummary(summary);
    }).fail(function () {
        $('#dry-run-summary').html('<p class="has-text-grey">No dry-run data available yet.</p>');
    });
    $.getJSON(`./dry_run/${name}`, function (history) {
        renderDryRunHistory(history);
    }).fail(function () {
        $('#dry-run-history').html('');
    });
}

function renderDryRunSummary(summary) {
    if (!summary || summary.length === 0) {
        $('#dry-run-summary').html('<p class="has-text-grey">No dry-run data available yet. Predictions will appear here after events resolve.</p>');
        return;
    }

    var html = '<table class="table is-fullwidth is-hoverable dry-run-table">';
    html += '<thead><tr>';
    html += '<th>Strategy</th><th>Total</th><th>Wins</th><th>Losses</th>';
    html += '<th>Win Rate</th><th>Net Points</th><th>Status</th>';
    html += '</tr></thead><tbody>';

    summary.forEach(function (s) {
        var rowClass = '';
        if (s.is_best) rowClass = 'dry-run-best';
        else if (s.is_active) rowClass = 'dry-run-active';

        var statusBadges = '';
        if (s.is_active) statusBadges += '<span class="tag is-warning is-light">ACTIVE</span> ';
        if (s.is_best) statusBadges += '<span class="tag is-success is-light">BEST</span>';
        if (!s.is_active && !s.is_best) statusBadges = '-';

        var pointsClass = s.net_points >= 0 ? 'has-text-success' : 'has-text-danger';
        var pointsPrefix = formatPointsPrefix(s.net_points);

        html += '<tr class="' + rowClass + '">';
        html += '<td><strong>' + s.strategy + '</strong></td>';
        html += '<td>' + s.total + '</td>';
        html += '<td>' + s.wins + '</td>';
        html += '<td>' + s.losses + '</td>';
        html += '<td>' + (s.win_rate != null ? s.win_rate + '%' : 'N/A') + '</td>';
        html += '<td class="' + pointsClass + '">' + pointsPrefix + s.net_points + '</td>';
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

    // Show last 10 predictions, most recent first
    var recent = history.slice(-10).reverse();

    var html = '<h4 class="title is-6" style="margin-bottom: 0.5rem;">Recent Predictions</h4>';
    html += '<div class="dry-run-history-list">';

    recent.forEach(function (pred) {
        var date = new Date(pred.x);
        var dateStr = date.toLocaleString();

        html += '<div class="dry-run-prediction-card">';
        html += '<div class="dry-run-prediction-header">';
        html += '<strong>' + (pred.event_title || 'Prediction') + '</strong>';
        html += '<span class="has-text-grey is-size-7"> ' + dateStr + '</span>';
        html += '<span class="tag is-info is-light is-small" style="margin-left:0.5rem;">Active: ' + (pred.active_strategy || '-') + '</span>';
        html += '</div>';
        html += '<div class="dry-run-prediction-strategies">';

        if (pred.strategies) {
            pred.strategies.forEach(function (s) {
                var icon = '';
                var cls = '';
                if (s.result_type === 'WIN') { icon = '✅'; cls = 'has-text-success'; }
                else if (s.result_type === 'LOSE') { icon = '❌'; cls = 'has-text-danger'; }
                else if (s.result_type === 'REFUND') { icon = '🔄'; cls = 'has-text-grey'; }
                else { icon = '⏳'; cls = 'has-text-grey'; }

                var isActive = s.strategy === pred.active_strategy;
                var activeMark = isActive ? ' <span class="tag is-warning is-light is-small">ACTIVE</span>' : '';
                var prefix = formatPointsPrefix(s.points_gained);

                html += '<span class="dry-run-strategy-item ' + cls + '">';
                html += icon + ' <strong>' + s.strategy + '</strong>';
                html += ' → ' + (s.outcome_title || 'Outcome ' + s.choice);
                if (s.result_type) html += ' (' + prefix + s.points_gained + ')';
                html += activeMark;
                html += '</span>';
            });
        }

        html += '</div></div>';
    });

    html += '</div>';
    $('#dry-run-history').html(html);
}

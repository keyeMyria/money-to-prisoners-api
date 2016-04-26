/* globals django, google, transactionReportData */

django.jQuery(function($) {
  'use strict';

  var $module = $('#id_transaction_report'),
    $chart = $('#transaction-report-chart'),
    normalStyles = {
      font: '"Roboto", "Lucida Grande", Verdana, Arial, sans-serif',
      lines: ['#79aec8', '#666'],
      lineWidth: 4,
      background: '#f9f9f9',
      weekendBackground: '#ddd',
      annotationTextStyle: {},
      lengendTextStyle: {
        color: '#000',
        fontSize: 13
      }
    },
    standoutStyles = {
      // colours and sizes optimised for philips tv
      font: '"Roboto", "Lucida Grande", Verdana, Arial, sans-serif',
      lines: ['#6f6', '#F3513F'],
      lineWidth: 10,
      background: '#111',
      weekendBackground: '#444',
      annotationTextStyle: {
        color: '#fff',
        fontSize: 36,
        fontWeight: 'bold'
      },
      lengendTextStyle: {
        color: '#fff',
        fontSize: 26
      }
    },
    chartData;

  if (!$chart.size()) {
    return;
  }

  google.charts.load('current', {packages: ['corechart']});
  google.charts.setOnLoadCallback(chartsLoaded);

  function chartsLoaded() {
    chartData = new google.visualization.DataTable();
    for (var column in transactionReportData.columns) {
      if (transactionReportData.columns.hasOwnProperty(column)) {
        chartData.addColumn(transactionReportData.columns[column]);
      }
    }
    chartData.addRows(transactionReportData.rows);

    drawTransactionReports();
  }

  function drawTransactionReports() {
    var chart = new google.visualization.LineChart($chart[0]),
      styles = $module.hasClass('mtp-dashboard-module-standout') ? standoutStyles : normalStyles;

    chart.draw(chartData, {
      hAxis: {
        baselineColor: 'none',
        gridlines: {count: 0}
      },
      vAxis: {
        minValue: 0,
        baselineColor: 'none',
        gridlines: {count: 0}
      },
      chartArea: {
        top: 10,
        bottom: 10,
        left: 10
      },
      fontName: styles.font,
      annotations: {
        textStyle: styles.annotationTextStyle
      },
      legend: {
        alignment: 'center',
        textStyle: styles.lengendTextStyle
      },
      backgroundColor: styles.background,
      colors: styles.lines,
      lineWidth: styles.lineWidth
    });

    google.visualization.events.addListener(chart, 'ready', function() {
      var cli = chart.getChartLayoutInterface(),
        bounds = cli.getChartAreaBoundingBox(),
        dayWidth = bounds.width / transactionReportData.rows.length,
        dayHeight = bounds.height,
        dayTop = bounds.top,
        dayLeft,
        $chartRect,
        legendBounds = cli.getBoundingBox('legend'),
        $title;

      $title = $(document.createElementNS('http://www.w3.org/2000/svg', 'text'));
      $title.text(transactionReportData.title);
      $title.attr({
        'text-anchor': 'start',
        x: legendBounds.left,
        y: legendBounds.top - Math.floor(styles.lengendTextStyle.fontSize / 2),
        fill: styles.lengendTextStyle.color,
        'font-family': styles.font,
        'font-weight': 'bold',
        'font-size': styles.lengendTextStyle.fontSize
      });
      $chart.find('svg').append($title);

      $chart.find('rect').each(function() {
        if (this.x.baseVal.value == bounds.left &&
          this.y.baseVal.value == bounds.top &&
          this.width.baseVal.value == bounds.width &&
          this.height.baseVal.value == bounds.height) {
          $chartRect = $(this);
        }
      });

      if ($chartRect.size()) {
        for (var weekend in transactionReportData.weekends) {
          if (!transactionReportData.weekends.hasOwnProperty(weekend)) {
            continue;
          }
          weekend = transactionReportData.weekends[weekend];
          dayLeft = cli.getXLocation(weekend) - dayWidth / 2;
          var $rect = $(document.createElementNS('http://www.w3.org/2000/svg', 'rect'));
          $rect.attr({
            x: dayLeft,
            y: dayTop,
            height: dayHeight,
            width: dayWidth,
            stroke: 'none',
            'stroke-width': 0,
            fill: styles.weekendBackground,
            'fill-opacity': 0.5
          });
          $chartRect.after($rect);
        }
      }

    });
  }

  $module.on('mtp.dashboard-standout', function() {
    $chart.empty();
    drawTransactionReports();
  })
});
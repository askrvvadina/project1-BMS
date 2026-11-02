document.addEventListener('DOMContentLoaded', function () {
    if (typeof Chart === 'undefined') {
        return;
    }

    function readChartData(elementId) {
        var element = document.getElementById(elementId);
        if (!element) {
            return {labels: [], values: []};
        }
        try {
            return JSON.parse(element.textContent);
        } catch (error) {
            return {labels: [], values: []};
        }
    }

    function createChart(canvasId, elementId, title) {
        var canvas = document.getElementById(canvasId);
        if (!canvas) {
            return;
        }
        var data = readChartData(elementId);
        var hasSales = data.values.some(function (value) { return Number(value) !== 0; });
        if (!hasSales) {
            canvas.style.display = 'none';
            return;
        }
        try {
        new Chart(canvas, {
            type: 'line',
            data: {
                labels: data.labels,
                datasets: [{
                    label: title,
                    data: data.values,
                    borderColor: '#3454d1',
                    backgroundColor: 'rgba(52, 84, 209, 0.12)',
                    borderWidth: 2,
                    pointRadius: 2,
                    pointHoverRadius: 4,
                    fill: true,
                    tension: 0.25
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                interaction: {intersect: false, mode: 'index'},
                plugins: {
                    legend: {display: false},
                    tooltip: {
                        callbacks: {
                            label: function (context) {
                                return context.parsed.y.toLocaleString('ru-RU', {minimumFractionDigits: 2, maximumFractionDigits: 2}) + ' сом';
                            }
                        }
                    }
                },
                scales: {
                    y: {
                        beginAtZero: true,
                        ticks: {
                            callback: function (value) { return value.toLocaleString('ru-RU') + ' сом'; }
                        }
                    }
                }
            }
        });
        } catch (error) {
            canvas.style.display = 'none';
        }
    }

    createChart('dailySalesChart', 'dailySalesData', 'Выручка за день');
    createChart('monthlySalesChart', 'monthlySalesData', 'Выручка за месяц');

    var categoryCanvas = document.getElementById('categorySalesChart');
    var categoryElement = document.getElementById('categorySalesData');
    if (categoryCanvas && categoryElement) {
        var categoryData;
        try {
            categoryData = JSON.parse(categoryElement.textContent);
        } catch (error) {
            categoryData = [];
        }
        if (categoryData.length > 0) {
            try {
            new Chart(categoryCanvas, {
                type: 'doughnut',
                data: {
                    labels: categoryData.map(function (item) { return item.category_name; }),
                    datasets: [{
                        data: categoryData.map(function (item) { return item.gross_sales; }),
                        backgroundColor: ['#3454d1', '#5c8fef', '#4d8d74', '#d9a441', '#9b6ab8', '#c66c6c']
                    }]
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    plugins: {
                        tooltip: {
                            callbacks: {
                                label: function (context) {
                                    return context.label + ': ' + context.parsed.toLocaleString('ru-RU', {minimumFractionDigits: 2, maximumFractionDigits: 2}) + ' сом';
                                }
                            }
                        }
                    }
                }
            });
            } catch (error) {
                categoryCanvas.style.display = 'none';
            }
        }
    }
});

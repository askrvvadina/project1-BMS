document.addEventListener('DOMContentLoaded', function () {
    const sidebarToggle = document.getElementById('sidebarToggle');
    const sidebar = document.getElementById('sidebar');

    if (!sidebarToggle || !sidebar) {
        return;
    }

    const closeSidebar = function () {
        sidebar.classList.remove('is-open');
        sidebarToggle.setAttribute('aria-expanded', 'false');
    };

    sidebarToggle.addEventListener('click', function () {
        const isOpen = sidebar.classList.toggle('is-open');
        sidebarToggle.setAttribute('aria-expanded', String(isOpen));
    });

    const navLinks = sidebar.querySelectorAll('.nav-item');
    navLinks.forEach(function (link) {
        link.addEventListener('click', function () {
            if (window.innerWidth <= 860) {
                closeSidebar();
            }
        });
    });

    document.addEventListener('click', function (event) {
        const isMobile = window.innerWidth <= 860;
        const clickInsideSidebar = sidebar.contains(event.target);
        const clickToggle = sidebarToggle.contains(event.target);

        if (isMobile && sidebar.classList.contains('is-open') && !clickInsideSidebar && !clickToggle) {
            closeSidebar();
        }
    });

    window.addEventListener('resize', function () {
        if (window.innerWidth > 860) {
            closeSidebar();
        }
    });
});

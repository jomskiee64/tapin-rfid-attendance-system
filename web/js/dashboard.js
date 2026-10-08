const dashboardApiBaseUrl = (window.TAPIN_API_URL || '').replace(/\/+$/, '');

// The logged-in admin's own user record, populated once the session is verified
let currentAdminUser = null;

/* ============ WORK STATUS REQUEST STATE (declared up front) ============ */

// Store work status requests (pending + approved + rejected) and derived state.
// These MUST be declared before any function that reads them runs — otherwise
// loadWorkStatusRequests() throws "Cannot access 'workStatusRequests' before initialization"
// and the whole work status pipeline silently dies.
let workStatusRequests = [];
let filteredWorkStatusRequests = [];
let workStatusRequestsCurrentPage = 1;
const WORK_STATUS_REQUESTS_PER_PAGE = 5;
let workStatusRequestFilters = {
    employeeSearch: ''
};
let workStatusRequestSort = {
    field: 'start_date',
    direction: 'asc'
};

// Work Status Request employee search dropdown state
let workStatusRequestSelectedSuggestionIndex = -1;

/* ============ END WORK STATUS REQUEST STATE ============ */

function redirectToLogin() {
    localStorage.removeItem('tapinUser');
    localStorage.removeItem('tapinToken');
    window.location.replace('../login.html');
}

function getAuthHeaders() {
    const token = localStorage.getItem('tapinToken');
    return {
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${token}`
    };
}

/* ============ PASSWORD TOGGLE ============ */

// Toggle a password input between hidden and visible, and swap the eye icon.
// Same behavior as the login page and employee dashboard password toggle.
function togglePasswordVisibility(inputId, buttonEl) {
    const input = document.getElementById(inputId);
    if (!input) return;

    const isPassword = input.type === 'password';
    input.type = isPassword ? 'text' : 'password';

    const icon = buttonEl ? buttonEl.querySelector('i') : null;
    if (icon) {
        icon.className = isPassword ? 'fa-solid fa-eye-slash' : 'fa-solid fa-eye';
    }
    if (buttonEl) {
        buttonEl.setAttribute('aria-label', isPassword ? 'Hide password' : 'Show password');
        buttonEl.setAttribute('title', isPassword ? 'Hide password' : 'Show password');
    }
}

/* Auth headers for multipart/form-data requests (file uploads).
   We must NOT set Content-Type — the browser sets it with the multipart
   boundary automatically. Only the Authorization token is sent. */
function getMultipartAuthHeaders() {
    const token = localStorage.getItem('tapinToken');
    return {
        'Authorization': `Bearer ${token}`
    };
}

/* ============ DTR / EMPLOYEE SEARCH DROPDOWN STYLES ============ */

// Inject the CSS that the DTR search dropdown needs to actually appear
// on top of the surrounding cards. Without these, the dropdown renders
// but is hidden behind the card below it (or clipped), which is exactly
// the "suggestion box doesn't show" bug.
function ensureDTRSearchStyles() {
    if (document.getElementById('dtrSearchStyles')) return;

    const style = document.createElement('style');
    style.id = 'dtrSearchStyles';
    style.textContent = `
        /* Wrapper that holds the icon + input + clear button. */
        .dtr-search-wrap {
            position: relative;
            display: flex;
            align-items: center;
            width: 100%;
        }

        /* Magnifying glass icon inside the input. */
        .dtr-search-wrap .dtr-search-icon {
            position: absolute;
            left: 12px;
            top: 50%;
            transform: translateY(-50%);
            color: var(--text-muted, #94a3b8);
            font-size: 13px;
            pointer-events: none;
            z-index: 1;
        }

        /* Leave room for the icon on the left and the clear button on the right. */
        .dtr-search-wrap .dtr-search-input {
            padding-left: 34px;
            padding-right: 34px;
            width: 100%;
        }

        /* Small × button that clears the search box. */
        .dtr-search-wrap .dtr-search-clear {
            position: absolute;
            right: 10px;
            top: 50%;
            transform: translateY(-50%);
            background: transparent;
            border: none;
            color: var(--text-muted, #94a3b8);
            cursor: pointer;
            display: flex;
            align-items: center;
            justify-content: center;
            padding: 2px;
            z-index: 1;
        }
        .dtr-search-wrap .dtr-search-clear:hover {
            color: var(--text, #0f172a);
        }

        /* THE DROPDOWN ITSELF — this is the piece that was missing
           proper positioning. position: absolute + z-index: 3000 makes it
           float above every card below the input. */
        .dtr-search-dropdown {
            position: absolute;
            top: calc(100% + 4px);
            left: 0;
            right: 0;
            background: var(--card-bg, #ffffff);
            border: 1px solid var(--border, #e5e7eb);
            border-radius: var(--radius, 8px);
            box-shadow: 0 10px 25px rgba(0, 0, 0, 0.15);
            max-height: 320px;
            overflow-y: auto;
            z-index: 3000;
            padding: 4px 0;
            scrollbar-width: thin;
        }

        /* Parent form-group must establish a positioning context for
           the absolutely-positioned dropdown, and must sit ABOVE
           sibling cards so the dropdown is not painted underneath them. */
        .form-group:has(> .dtr-search-wrap) {
            position: relative;
            z-index: 100;
        }

        /* Any card / card-body that contains a search dropdown must NOT
           clip it. This overrides the global .card-body { overflow:hidden }
           rule that was cutting the suggestion list off at the card edge. */
        .card:has(.dtr-search-dropdown),
        .card-body:has(.dtr-search-dropdown),
        .card:has(.dtr-search-wrap),
        .card-body:has(.dtr-search-wrap) {
            overflow: visible !important;
        }

        /* Each suggestion row. */
        .dtr-suggestion {
            display: flex;
            align-items: center;
            gap: 10px;
            padding: 8px 12px;
            cursor: pointer;
            transition: background 0.12s ease;
        }
        .dtr-suggestion:hover,
        .dtr-suggestion.active {
            background: var(--primary-light, #eff6ff);
        }

        .dtr-suggestion-avatar {
            flex-shrink: 0;
            width: 32px;
            height: 32px;
            border-radius: 50%;
            background: var(--primary, #2563eb);
            color: #fff;
            display: flex;
            align-items: center;
            justify-content: center;
            font-weight: 700;
            font-size: 12px;
        }

        .dtr-suggestion-body {
            flex: 1;
            min-width: 0;
        }

        .dtr-suggestion-name {
            font-size: 13px;
            font-weight: 600;
            color: var(--text, #0f172a);
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
        }

        .dtr-suggestion-meta {
            font-size: 11px;
            color: var(--text-muted, #94a3b8);
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
        }

        .dtr-suggestion-highlight {
            background: #fef3c7;
            color: #92400e;
            padding: 0 2px;
            border-radius: 3px;
        }

        .dtr-suggestion-badge {
            flex-shrink: 0;
            font-size: 10px;
            text-transform: uppercase;
            padding: 2px 8px;
            border-radius: 999px;
            font-weight: 700;
        }
        .dtr-suggestion-badge.role-admin { background: #fee2e2; color: #dc2626; }
        .dtr-suggestion-badge.role-hr { background: #dbeafe; color: #2563eb; }
        .dtr-suggestion-badge.role-employee { background: #dcfce7; color: #16a34a; }

        .dtr-suggestion-empty {
            padding: 14px;
            text-align: center;
            font-size: 12px;
            color: var(--text-muted, #94a3b8);
        }
    `;
    document.head.appendChild(style);
}

/* ============ END DTR / EMPLOYEE SEARCH DROPDOWN STYLES ============ */

function updateUserDisplay(user) {
    const name = document.getElementById('dashboardUserName');
    const role = document.getElementById('dashboardUserRole');
    if (name) name.textContent = user.fullname || user.username || 'User';
    if (role) role.textContent = (user.role || 'employee').toUpperCase();
}

function updateDeviceDisplay(devices) {
    const indicator = document.getElementById('readerStatusIndicator');
    const statusText = document.getElementById('readerStatusText');
    const online = devices.length > 0;

    if (indicator) {
        indicator.className = `status-indicator ${online ? 'status-online' : 'status-offline'}`;
    }
    if (statusText) {
        statusText.className = `status-text ${online ? 'text-online' : 'text-offline'}`;
        statusText.innerHTML = online
            ? '<i class="fa-solid fa-circle-check"></i> Online'
            : '<i class="fa-solid fa-circle-xmark"></i> Offline';
    }
}

function updateClock() {
    const now = new Date();
    const date = document.getElementById('dashboardDate');
    const time = document.getElementById('dashboardTime');
    if (date) date.textContent = now.toLocaleDateString(undefined, {
        weekday: 'long', year: 'numeric', month: 'long', day: 'numeric'
    });
    if (time) time.textContent = now.toLocaleTimeString(undefined, {
        hour: '2-digit', minute: '2-digit', second: '2-digit', timeZoneName: 'short'
    });
}

function updateDashboardStatistics(stats) {
    const values = {
        statTotalEmployees: stats.total_employees,
        statPresentToday: stats.present_today,
        statAbsentToday: stats.absent_today,
        statEmployeesLate: stats.employees_late,
        statOnWorkStatus: stats.on_work_status,
        statAttendanceRate: `${stats.attendance_rate}%`,
        // RFID scans today — RFID taps only (separate counter from facial)
        statRfidScans: stats.rfid_scans_today,
        // Facial scans today — face-recognition matches only
        statFaceScans: stats.face_scans_today,
        statDepartments: stats.departments
    };
    Object.entries(values).forEach(([id, value]) => {
        const element = document.getElementById(id);
        if (element) element.textContent = value;
    });
    
    // Update progress bars based on stats
    updateStatProgressBars(stats);
}

// Update stat card progress bars based on actual data
function updateStatProgressBars(stats) {
    const total = stats.total_employees || 1;
    const present = stats.present_today || 0;
    const absent = stats.absent_today || 0;
    const onWorkStatus = stats.on_work_status || 0;
    const attendanceRate = stats.attendance_rate || 0;
    const rfidScans = stats.rfid_scans_today || 0;
    const faceScans = stats.face_scans_today || 0;
    
    // Total Employees bar (always full)
    const totalBar = document.querySelector('.stat-blue .stat-progress-bar');
    if (totalBar) totalBar.style.width = '100%';
    
    // Present Today bar
    const presentBar = document.querySelector('.stat-green .stat-progress-bar');
    if (presentBar) {
        const pct = total > 0 ? (present / total) * 100 : 0;
        presentBar.style.width = `${Math.min(pct, 100)}%`;
    }
    
    // Absent Today bar
    const absentBar = document.querySelector('.stat-red .stat-progress-bar');
    if (absentBar) {
        const pct = total > 0 ? (absent / total) * 100 : 0;
        absentBar.style.width = `${Math.min(pct, 100)}%`;
    }
    
    // On Work Status bar
    const workStatusBar = document.querySelector('.stat-purple .stat-progress-bar');
    if (workStatusBar) {
        const pct = total > 0 ? (onWorkStatus / total) * 100 : 0;
        workStatusBar.style.width = `${Math.min(pct, 100)}%`;
    }
    
    // Attendance Rate bar (not currently shown but kept for safety)
    const rateBar = document.querySelector('.stat-cyan .stat-progress-bar');
    if (rateBar) {
        rateBar.style.width = `${Math.min(attendanceRate, 100)}%`;
    }
    
    // RFID Scans bar — first stat-blue card with progress bar after Present/Absent.
    // We use a dedicated selector on the RFID card itself if it has one,
    // otherwise fall back to the last .stat-blue block.
    const rfidBar = document.getElementById('statRfidScans')
        ? document.getElementById('statRfidScans').closest('.stat-card')?.querySelector('.stat-progress-bar')
        : document.querySelector('.stat-blue:last-child .stat-progress-bar');
    if (rfidBar) {
        // Use a reasonable max (e.g., 100 scans) or scale dynamically
        const maxScans = 100;
        const pct = Math.min((rfidScans / maxScans) * 100, 100);
        rfidBar.style.width = `${pct}%`;
    }

    // Facial Scans bar — NEW. Uses its own card so it does not share
    // the RFID progress bar even when both cards use similar styling.
    const faceBar = document.getElementById('statFaceScans')
        ? document.getElementById('statFaceScans').closest('.stat-card')?.querySelector('.stat-progress-bar')
        : null;
    if (faceBar) {
        const maxScans = 100;
        const pct = Math.min((faceScans / maxScans) * 100, 100);
        faceBar.style.width = `${pct}%`;
    }
}

// Format a date value (e.g. "2026-09-01", "2026-09-01T00:00:00Z", or a Date)
// into the DTR "D - Mon" style (e.g. "1 - Sep"). Works for any month/year —
// nothing here is hard-coded to a specific month.
const DTR_MONTH_ABBR = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
function formatDTRDate(value) {
    if (!value && value !== 0) return '';

    // ISO-style "YYYY-MM-DD" (optionally with a time/zone suffix) — parse the
    // digits directly so we never lose a day to a timezone shift.
    const isoMatch = String(value).match(/^(\d{4})-(\d{2})-(\d{2})/);
    if (isoMatch) {
        const day = parseInt(isoMatch[3], 10);
        const monthIndex = parseInt(isoMatch[2], 10) - 1;
        const monthAbbr = DTR_MONTH_ABBR[monthIndex] || '';
        return monthAbbr ? `${day} - ${monthAbbr}` : String(value);
    }

    // A real Date object.
    if (value instanceof Date && !isNaN(value.getTime())) {
        return `${value.getDate()} - ${DTR_MONTH_ABBR[value.getMonth()]}`;
    }

    // Already formatted (e.g. "1 - Sep") or some other string — leave as-is.
    return String(value);
}

// Sort employees by uid (numeric or string)
function sortEmployeesByUid(employees) {
    return employees.slice().sort((a, b) => {
        const uidA = a.uid || '';
        const uidB = b.uid || '';

        // Try to parse as numbers for numeric sorting
        const numA = parseInt(uidA, 10);
        const numB = parseInt(uidB, 10);

        if (!isNaN(numA) && !isNaN(numB)) {
            return numA - numB;
        }

        // Fallback to string comparison
        return uidA.localeCompare(uidB);
    });
}

// Format a date value into short "M/D/YY" style used in the DTR header
// (e.g. "9/1/26"), no leading zeros — same style as the reference form.
function formatDTRHeaderDate(value) {
    if (!value && value !== 0) return '';

    // ISO "YYYY-MM-DD" (optionally with a time/zone suffix).
    let isoMatch = String(value).match(/^(\d{4})-(\d{2})-(\d{2})/);
    if (isoMatch) {
        const year = isoMatch[1].slice(-2);
        const month = parseInt(isoMatch[2], 10);
        const day = parseInt(isoMatch[3], 10);
        return `${month}/${day}/${year}`;
    }

    // Already "M/D/YY" or "MM/DD/YY" — normalize away any leading zeros.
    let shortMatch = String(value).match(/^(\d{1,2})\/(\d{1,2})\/(\d{2,4})$/);
    if (shortMatch) {
        const month = parseInt(shortMatch[1], 10);
        const day = parseInt(shortMatch[2], 10);
        const year = shortMatch[3].length > 2 ? shortMatch[3].slice(-2) : shortMatch[3];
        return `${month}/${day}/${year}`;
    }

    if (value instanceof Date && !isNaN(value.getTime())) {
        return `${value.getMonth() + 1}/${value.getDate()}/${String(value.getFullYear()).slice(-2)}`;
    }

    return String(value);
}

// Work out the "From : To :" range for the DTR header. Prefers the record's
// own from_date/to_date (whatever month was selected), falls back to the
// first/last day actually present in the dtr rows, and only falls back to
// today's calendar month if neither is available — so it always reflects
// the month being viewed, for any month, not just the current one.
function resolveDTRDateRange(record, dtr) {
    let from = record && record.from_date;
    let to = record && record.to_date;

    if (!from && dtr && dtr.length) from = dtr[0].date;
    if (!to && dtr && dtr.length) to = dtr[dtr.length - 1].date;

    if (!from || !to) {
        const now = new Date();
        const firstDay = new Date(now.getFullYear(), now.getMonth(), 1);
        const lastDay = new Date(now.getFullYear(), now.getMonth() + 1, 0);
        from = from || firstDay;
        to = to || lastDay;
    }

    return {
        from: formatDTRHeaderDate(from),
        to: formatDTRHeaderDate(to)
    };
}

function escapeHtml(value) {
    return String(value ?? '').replace(/[&<>'"]/g, (character) => ({
        '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;'
    }[character]));
}

function initials(user) {
    return `${user.firstname || ''} ${user.lastname || ''}`.trim().split(/\s+/).map((part) => part[0] || '').join('').slice(0, 2).toUpperCase() || '--';
}

// Normalize employment type, functional role, and gender values from display format to option value format
function normalizeEmploymentType(value) {
    if (!value) return '';
    const lower = value.toLowerCase();
    const map = {
        'permanent / regular': 'permanent_regular',
        'job order (jo)': 'job_order',
        'contract of service (cos)': 'contract_service',
        'provisional / temporary': 'provisional_temporary',
        'substitute / casual': 'substitute_casual'
    };
    return map[lower] || lower.replace(/[\s\/]+/g, '_').replace(/\([^)]*\)/, '').replace(/^_|_$/g, '');
}

function normalizeFunctionalRole(value) {
    if (!value) return '';
    const lower = value.toLowerCase();
    const map = {
        'teaching personnel': 'teaching',
        'administrative & clerical staff': 'administrative',
        'student services personnel': 'student_services',
        'facilities & security personnel': 'facilities_security'
    };
    return map[lower] || lower.replace(/[&\s]+/g, '_').replace(/\([^)]*\)/, '').replace(/^_|_$/g, '');
}

function normalizeGender(value) {
    if (!value) return '';
    const lower = value.toLowerCase();
    const map = {
        'male': 'male',
        'female': 'female',
        'other': 'other'
    };
    return map[lower] || lower;
}

// Normalize department values from display format to option value format
function normalizeDepartment(value) {
    if (!value) return '';
    // Take the part before the "•" if it exists, otherwise use the whole value
    const parts = value.split('•');
    const firstPart = parts[0].trim();
    // Convert to lowercase
    return firstPart.toLowerCase();
}

// Normalize position values from display format to option value format
function normalizePosition(value) {
    if (!value) return '';
    // Convert to lowercase and replace spaces with underscores
    return value.toLowerCase().replace(/\s+/g, '_');
}

// Normalize role values from display format to option value format
function normalizeRole(value) {
    if (!value) return '';
    // Convert to lowercase
    return value.toLowerCase();
}

// ============ SEARCH FUNCTIONALITY ============

// Store search results for autocomplete
let searchSuggestions = [];
let selectedSuggestionIndex = -1;

// Initialize search functionality
function initSearch() {
    const searchInput = document.querySelector('.search-input');
    if (!searchInput) return;
    
    // Create autocomplete dropdown container
    let dropdown = document.getElementById('searchAutocomplete');
    if (!dropdown) {
        dropdown = document.createElement('div');
        dropdown.id = 'searchAutocomplete';
        dropdown.style.cssText = `
            position: absolute;
            top: 100%;
            left: 0;
            right: 0;
            background: var(--card-bg);
            border: 1px solid var(--border);
            border-radius: var(--radius);
            box-shadow: var(--shadow-lg);
            max-height: 320px;
            overflow-y: auto;
            z-index: 1000;
            display: none;
            margin-top: 4px;
        `;
        // Add scrollbar styling
        dropdown.style.scrollbarWidth = 'thin';
        dropdown.style.scrollbarColor = 'var(--border-dark) transparent';
        searchInput.parentNode.style.position = 'relative';
        searchInput.parentNode.appendChild(dropdown);
    }
    
    // Search on input with debounce
    let searchTimeout;
    searchInput.addEventListener('input', function() {
        clearTimeout(searchTimeout);
        const searchTerm = this.value.trim();
        selectedSuggestionIndex = -1;
        
        if (searchTerm.length >= 1) {
            searchTimeout = setTimeout(() => {
                performSearch(searchTerm);
            }, 300);
        } else {
            dropdown.style.display = 'none';
            clearSearchHighlights();
            searchSuggestions = [];
        }
    });
    
    // Search on Enter key - go to first result
    searchInput.addEventListener('keydown', function(event) {
        const items = dropdown.querySelectorAll('.search-suggestion-item');
        
        if (event.key === 'Enter') {
            event.preventDefault();
            const searchTerm = this.value.trim();
            if (searchTerm) {
                // If an item is selected via arrow keys, go to that
                if (selectedSuggestionIndex >= 0 && items.length > 0) {
                    const selectedItem = items[selectedSuggestionIndex];
                    const sectionId = selectedItem.dataset.sectionId;
                    if (sectionId) {
                        goToSearchResult(sectionId);
                        dropdown.style.display = 'none';
                        this.value = '';
                        clearSearchHighlights();
                        return;
                    }
                }
                // Otherwise go to first result
                if (searchSuggestions.length > 0) {
                    goToSearchResult(searchSuggestions[0].id);
                }
                dropdown.style.display = 'none';
                this.value = '';
                clearSearchHighlights();
            }
        }
        
        // Arrow key navigation
        if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
            event.preventDefault();
            if (items.length === 0) return;
            
            // Remove active class from all items
            items.forEach(item => item.classList.remove('active'));
            
            if (event.key === 'ArrowDown') {
                selectedSuggestionIndex = Math.min(selectedSuggestionIndex + 1, items.length - 1);
            } else {
                selectedSuggestionIndex = Math.max(selectedSuggestionIndex - 1, 0);
            }
            
            items[selectedSuggestionIndex].classList.add('active');
            // Scroll to the active item
            items[selectedSuggestionIndex].scrollIntoView({ block: 'nearest' });
        }
        
        // Escape key - close dropdown
        if (event.key === 'Escape') {
            dropdown.style.display = 'none';
            clearSearchHighlights();
            this.value = '';
            searchSuggestions = [];
            selectedSuggestionIndex = -1;
        }
    });
    
    // Close dropdown when clicking outside
    document.addEventListener('click', function(event) {
        if (!searchInput.parentNode.contains(event.target)) {
            dropdown.style.display = 'none';
        }
    });
}

// Perform search across all sections
function performSearch(searchTerm) {
    const term = searchTerm.toLowerCase().trim();
    if (!term) {
        hideAutocomplete();
        clearSearchHighlights();
        return;
    }
    
    // Clear previous highlights
    clearSearchHighlights();
    
    // Search in all sections
    const sections = document.querySelectorAll('.page-section');
    let matchedSections = [];
    let totalMatches = 0;
    
    sections.forEach(section => {
        const sectionText = section.textContent.toLowerCase();
        if (sectionText.includes(term)) {
            const sectionId = section.id;
            if (sectionId) {
                const title = section.querySelector('.section-title')?.textContent || 
                             section.querySelector('h1')?.textContent ||
                             sectionId.replace('-', ' ').toUpperCase();
                const matchCount = countMatchesInElement(section, term);
                matchedSections.push({
                    id: sectionId,
                    title: title,
                    element: section,
                    matchCount: matchCount
                });
                totalMatches += matchCount;
                
                // Highlight matching section
                section.style.border = '3px solid var(--primary)';
                section.style.boxShadow = '0 0 20px rgba(37,99,235,0.3)';
                section.style.transition = 'all 0.5s ease';
                section.classList.add('search-match');
                
                // Highlight matching text within the section
                highlightTextInElement(section, term);
            }
        }
    });
    
    // Also search employees
    const employeeMatches = searchEmployees(term);
    if (employeeMatches) {
        totalMatches += employeeMatches;
    }
    
    // Store suggestions and show autocomplete
    searchSuggestions = matchedSections;
    if (matchedSections.length > 0) {
        showAutocomplete(term, matchedSections, totalMatches);
    } else if (totalMatches > 0) {
        // If only employee matches, show a message
        showAutocomplete(term, [], totalMatches);
    } else {
        showAutocomplete(term, [], 0);
    }
}

// Count matches in an element
function countMatchesInElement(element, term) {
    let count = 0;
    const walker = document.createTreeWalker(
        element,
        NodeFilter.SHOW_TEXT,
        {
            acceptNode: function(node) {
                if (node.parentElement.tagName === 'SCRIPT' || 
                    node.parentElement.tagName === 'STYLE' ||
                    node.parentElement.tagName === 'INPUT' ||
                    node.parentElement.tagName === 'TEXTAREA' ||
                    node.parentElement.tagName === 'SELECT') {
                    return NodeFilter.FILTER_REJECT;
                }
                return NodeFilter.FILTER_ACCEPT;
            }
        }
    );
    
    let currentNode;
    while (currentNode = walker.nextNode()) {
        const text = currentNode.textContent.toLowerCase();
        const matches = (text.match(new RegExp(term, 'gi')) || []).length;
        count += matches;
    }
    return count;
}

// Highlight text within an element
function highlightTextInElement(element, term) {
    const walker = document.createTreeWalker(
        element,
        NodeFilter.SHOW_TEXT,
        {
            acceptNode: function(node) {
                if (node.parentElement.tagName === 'SCRIPT' || 
                    node.parentElement.tagName === 'STYLE' ||
                    node.parentElement.tagName === 'INPUT' ||
                    node.parentElement.tagName === 'TEXTAREA' ||
                    node.parentElement.tagName === 'SELECT') {
                    return NodeFilter.FILTER_REJECT;
                }
                return NodeFilter.FILTER_ACCEPT;
            }
        }
    );
    
    const nodesToReplace = [];
    let currentNode;
    while (currentNode = walker.nextNode()) {
        if (currentNode.textContent.toLowerCase().includes(term)) {
            nodesToReplace.push(currentNode);
        }
    }
    
    nodesToReplace.forEach(node => {
        const parent = node.parentNode;
        const text = node.textContent;
        const parts = text.split(new RegExp(`(${term})`, 'gi'));
        
        const fragment = document.createDocumentFragment();
        parts.forEach(part => {
            if (part && part.toLowerCase() === term.toLowerCase()) {
                const span = document.createElement('span');
                span.textContent = part;
                span.style.backgroundColor = '#FEF3C7';
                span.style.color = '#92400E';
                span.style.padding = '1px 3px';
                span.style.borderRadius = '3px';
                span.style.fontWeight = '700';
                span.className = 'search-highlight';
                fragment.appendChild(span);
            } else if (part) {
                const textNode = document.createTextNode(part);
                fragment.appendChild(textNode);
            }
        });
        
        parent.replaceChild(fragment, node);
    });
}

// Clear search highlights
function clearSearchHighlights() {
    // Remove border highlights from sections
    document.querySelectorAll('.page-section').forEach(section => {
        section.style.border = '';
        section.style.boxShadow = '';
        section.style.transition = '';
        section.classList.remove('search-match');
    });
    
    // Remove text highlights
    document.querySelectorAll('.search-highlight').forEach(el => {
        const parent = el.parentNode;
        const text = el.textContent;
        const textNode = document.createTextNode(text);
        parent.replaceChild(textNode, el);
        parent.normalize();
    });
}

// Show autocomplete dropdown
function showAutocomplete(searchTerm, matchedSections, totalMatches) {
    const dropdown = document.getElementById('searchAutocomplete');
    if (!dropdown) return;
    
    let html = '';
    
    // Header with search info
    html += `
        <div style="padding:8px 14px;border-bottom:1px solid var(--border);font-size:12px;color:var(--text-muted);background:var(--bg);border-radius:var(--radius) var(--radius) 0 0;">
            <strong style="color:var(--text);">${totalMatches}</strong> result${totalMatches !== 1 ? 's' : ''} found for "<strong>${escapeHtml(searchTerm)}</strong>"
        </div>
    `;
    
    if (matchedSections.length > 0) {
        matchedSections.forEach((section, index) => {
            const isActive = index === selectedSuggestionIndex;
            html += `
                <div class="search-suggestion-item ${isActive ? 'active' : ''}" 
                     data-section-id="${escapeHtml(section.id)}"
                     style="padding:8px 14px;cursor:pointer;display:flex;justify-content:space-between;align-items:center;border-bottom:1px solid var(--border);transition:background 0.15s ease;${isActive ? 'background:var(--primary-light);' : ''}"
                     onmouseenter="this.style.background='var(--primary-light)'" 
                     onmouseleave="this.style.background='${isActive ? 'var(--primary-light)' : 'transparent'}'"
                     onclick="goToSearchResult('${escapeHtml(section.id)}')">
                    <div style="display:flex;align-items:center;gap:10px;flex:1;min-width:0;">
                        <i class="fa-solid fa-file-lines" style="color:var(--primary);font-size:13px;flex-shrink:0;"></i>
                        <span style="font-weight:500;font-size:13px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">${escapeHtml(section.title)}</span>
                    </div>
                    <span class="badge badge-info" style="font-size:10px;flex-shrink:0;background:var(--primary-light);color:var(--primary);">${section.matchCount} match${section.matchCount !== 1 ? 'es' : ''}</span>
                </div>
            `;
        });
    } else if (totalMatches > 0) {
        // Only employee matches
        html += `
            <div style="padding:12px 14px;color:var(--text-muted);text-align:center;font-size:13px;">
                <i class="fa-solid fa-users" style="margin-right:8px;color:var(--primary);"></i>
                Found ${totalMatches} employee(s) matching "<strong>${escapeHtml(searchTerm)}</strong>"
                <br><span style="font-size:12px;">Check the Employee Directory section</span>
            </div>
        `;
    } else {
        html += `
            <div style="padding:12px 14px;color:var(--text-muted);text-align:center;font-size:13px;">
                <i class="fa-solid fa-search" style="margin-right:8px;opacity:0.5;"></i>
                No results found for "<strong>${escapeHtml(searchTerm)}</strong>"
            </div>
        `;
    }
    
    dropdown.innerHTML = html;
    dropdown.style.display = 'block';
}

// Hide autocomplete dropdown
function hideAutocomplete() {
    const dropdown = document.getElementById('searchAutocomplete');
    if (dropdown) {
        dropdown.style.display = 'none';
    }
}

// Go to a specific search result
function goToSearchResult(sectionId) {
    if (sectionId) {
        // Close dropdown
        hideAutocomplete();
        // Clear search input
        const searchInput = document.querySelector('.search-input');
        if (searchInput) searchInput.value = '';
        // Scroll to section
        scrollToSection(`#${sectionId}`);
        setActiveNavForHash(`#${sectionId}`);
        // Highlight the selected section
        document.querySelectorAll('.page-section').forEach(s => {
            s.style.border = '';
            s.style.boxShadow = '';
        });
        const selectedSection = document.getElementById(sectionId);
        if (selectedSection) {
            selectedSection.style.border = '3px solid var(--primary)';
            selectedSection.style.boxShadow = '0 0 20px rgba(37,99,235,0.3)';
            // Remove highlight after 3 seconds
            setTimeout(() => {
                selectedSection.style.border = '';
                selectedSection.style.boxShadow = '';
            }, 3000);
        }
        // Clear highlights after 4 seconds
        setTimeout(() => {
            clearSearchHighlights();
        }, 4000);
    }
}

// Search for employees in the directory
function searchEmployees(term) {
    // Check if we have employee data
    if (allEmployees.length === 0) {
        return 0;
    }
    
    const matchingEmployees = allEmployees.filter(emp => {
        const fullname = `${emp.firstname || ''} ${emp.lastname || ''}`.toLowerCase();
        const employeeId = (emp.employeeid || '').toLowerCase();
        const email = (emp.email || '').toLowerCase();
        const department = (emp.department || '').toLowerCase();
        
        return fullname.includes(term) || 
               employeeId.includes(term) || 
               email.includes(term) ||
               department.includes(term);
    });
    
    if (matchingEmployees.length > 0) {
        // Navigate to employee section
        scrollToSection('#employees');
        setActiveNavForHash('#employees');
        
        // Apply filter to show matching employees
        const searchInput = document.getElementById('employeeSearchInput');
        if (searchInput) {
            searchInput.value = term;
            // Show filter bar if hidden
            const filterBar = document.getElementById('employeeFilterBar');
            if (filterBar && filterBar.style.display === 'none') {
                filterBar.style.display = 'block';
                const btn = document.querySelector('.section-actions .btn-outline');
                if (btn) {
                    btn.innerHTML = '<i class="fa-solid fa-filter"></i> Hide Filter';
                }
            }
            // Trigger filter
            filterEmployees();
        }
        
        // Highlight the employee section
        const employeeSection = document.getElementById('employees');
        if (employeeSection) {
            employeeSection.style.border = '3px solid var(--primary)';
            employeeSection.style.boxShadow = '0 0 20px rgba(37,99,235,0.3)';
            setTimeout(() => {
                employeeSection.style.border = '';
                employeeSection.style.boxShadow = '';
            }, 3000);
        }
        
        return matchingEmployees.length;
    }
    
    return 0;
}

// ============ END SEARCH FUNCTIONALITY ============

// ============ EMPLOYEE DIRECTORY FUNCTIONS ============

// Store all employees for filtering
let allEmployees = [];
let filteredEmployees = [];
let currentPage = 1;
const CARDS_PER_PAGE = 5;
let previousEmployeesData = '';

// Update the employees grid with cards
function updateEmployeesGrid(users) {
    const grid = document.getElementById('employeesGrid');
    if (!grid) return;
    
    // Store all employees for filtering (only update if data changed)
    const currentData = JSON.stringify(users || []);
    if (currentData !== previousEmployeesData) {
        // Sort employees by uid before storing
        allEmployees = sortEmployeesByUid(users || []);
        previousEmployeesData = JSON.stringify(allEmployees);
        // Populate department filter dropdown
        populateDepartmentFilter(allEmployees);

        // Only reset filters on the FIRST load (when we have no filtered
        // list yet). On subsequent server refreshes we keep whatever the
        // user has selected so the cards don't vanish and filters don't
        // silently reset for every role.
        const isFirstLoad = !filteredEmployees || filteredEmployees.length === 0;

        if (isFirstLoad) {
            const roleFilterEl = document.getElementById('employeeRoleFilter');
            const deptFilterEl = document.getElementById('employeeDeptFilter');
            const searchInputEl = document.getElementById('employeeSearchInput');
            if (roleFilterEl) roleFilterEl.value = 'all';
            if (deptFilterEl) deptFilterEl.value = 'all';
            if (searchInputEl) searchInputEl.value = '';
            // Reset to page 1 when new data loads
            currentPage = 1;
            // Apply the (now reset) filters so filteredEmployees is populated
            filterEmployees();
        } else {
            // Re-apply the CURRENT filter inputs to the new allEmployees
            // list without touching the inputs themselves. This keeps the
            // grid stable across the 5-second dashboard refresh.
            applyCurrentEmployeeFilters();
        }
    }
    
    // Render the grid
    renderEmployeeCards(filteredEmployees);
}

// Populate department filter dropdown with unique departments
function populateDepartmentFilter(employees) {
    const deptFilter = document.getElementById('employeeDeptFilter');
    if (!deptFilter) return;
    
    // Get unique departments
    const departments = new Set();
    employees.forEach(emp => {
        if (emp.department && emp.department.trim()) {
            departments.add(emp.department.trim());
        }
    });
    
    // Clear existing options except the first one (All Departments)
    while (deptFilter.options.length > 1) {
        deptFilter.remove(1);
    }
    
    // Sort departments alphabetically
    const sortedDepts = Array.from(departments).sort();
    
    // Add departments to dropdown
    sortedDepts.forEach(dept => {
        const option = document.createElement('option');
        option.value = dept;
        option.textContent = dept;
        deptFilter.appendChild(option);
    });
}

// Render employee cards with pagination (5 cards per page)
function renderEmployeeCards(employees) {
    const grid = document.getElementById('employeesGrid');
    if (!grid) return;
    
    const footer = document.getElementById('employeeGridFooter');
    const countDisplay = document.getElementById('employeeCountDisplay');
    const paginationContainer = document.getElementById('employeePagination');
    
    if (!employees || employees.length === 0) {
        grid.innerHTML = `
            <div class="empty-state" style="grid-column:1/-1;padding:40px 20px;">
                <i class="fa-solid fa-users-slash"></i>
                <p style="font-size:14px;color:var(--text-muted);">No employees found matching your filters.</p>
            </div>
        `;
        if (footer) footer.style.display = 'none';
        return;
    }
    
    // Calculate pagination
    const totalPages = Math.ceil(employees.length / CARDS_PER_PAGE);
    const startIndex = (currentPage - 1) * CARDS_PER_PAGE;
    const endIndex = Math.min(startIndex + CARDS_PER_PAGE, employees.length);
    const visibleEmployees = employees.slice(startIndex, endIndex);
    
    // Generate card HTML
    grid.innerHTML = visibleEmployees.map((user, index) => {
        const fullname = `${user.firstname || ''} ${user.lastname || ''}`.trim() || 'Unknown';
        const initialsText = fullname.split(' ').map(w => w[0] || '').join('').slice(0, 2).toUpperCase() || '--';
        const role = user.role || 'employee';
        const roleColor = role === 'admin' ? 'var(--danger)' : role === 'hr' ? 'var(--primary)' : 'var(--success)';
        const roleBg = role === 'admin' ? 'var(--danger-light)' : role === 'hr' ? 'var(--primary-light)' : 'var(--success-light)';
        const employeeId = user.employeeid || user.uid || 'N/A';
        const email = user.email || 'N/A';
        const department = user.department || 'N/A';
        
        return `
            <div class="emp-card" data-index="${startIndex + index}" data-role="${escapeHtml(role)}" data-name="${escapeHtml(fullname.toLowerCase())}" data-id="${escapeHtml(employeeId)}" data-email="${escapeHtml(email)}">
                <div class="emp-card-header" style="background:linear-gradient(135deg, ${roleColor}, ${role === 'admin' ? '#DC2626' : role === 'hr' ? '#2563EB' : '#16A34A'});">
                    <div class="emp-card-avatar" style="display:flex;align-items:center;justify-content:center;font-size:26px;font-weight:800;color:white;">
                        ${escapeHtml(initialsText)}
                    </div>
                    <div class="emp-card-name">${escapeHtml(fullname)}</div>
                    <div class="emp-card-pos">${escapeHtml(role.toUpperCase())}</div>
                </div>
                <div class="emp-card-body">
                    <div class="emp-card-info">
                        <div class="emp-info-row">
                            <i class="fa-solid fa-id-badge"></i>
                            <span class="emp-info-val">${escapeHtml(employeeId)}</span>
                        </div>
                        <div class="emp-info-row">
                            <i class="fa-solid fa-envelope"></i>
                            <span class="emp-info-val">${escapeHtml(email)}</span>
                        </div>
                        <div class="emp-info-row">
                            <i class="fa-solid fa-building"></i>
                            <span class="emp-info-val">${escapeHtml(department)}</span>
                        </div>
                        <div class="emp-info-row">
                            <i class="fa-solid fa-venus-mars"></i>
                            <span class="emp-info-val">${escapeHtml(user.gender || '--')}</span>
                        </div>
                        <div class="emp-info-row">
                            <i class="fa-solid fa-user-tag"></i>
                            <span class="emp-info-val" style="display:inline-flex;align-items:center;gap:6px;">
                                <span class="badge" style="background:${roleBg};color:${roleColor};font-size:10px;padding:2px 10px;">${escapeHtml(role.toUpperCase())}</span>
                            </span>
                        </div>
                    </div>
                    <div style="display:flex;gap:8px;margin-top:8px;">
                        <button class="btn btn-outline btn-sm" onclick="viewEmployee('${escapeHtml(user.uid || '')}')" style="flex:1;justify-content:center;">
                            <i class="fa-solid fa-eye"></i> View
                        </button>
                        <button class="btn btn-primary btn-sm" onclick="editEmployee('${escapeHtml(user.uid || '')}')" style="flex:1;justify-content:center;">
                            <i class="fa-solid fa-edit"></i> Edit
                        </button>
                    </div>
                </div>
            </div>
        `;
    }).join('');
    
    // Handle footer and pagination
    if (footer) {
        if (employees.length > CARDS_PER_PAGE) {
            footer.style.display = 'block';
            
            // Update count display
            if (countDisplay) {
                countDisplay.textContent = `Showing ${startIndex + 1} - ${endIndex} of ${employees.length} employees`;
            }
            
            // Generate pagination buttons - Updated with Prev/Next text
            if (paginationContainer) {
                let paginationHTML = '<div class="pagination-controls" style="display:flex;gap:6px;justify-content:center;align-items:center;flex-wrap:wrap;margin-top:8px;">';
                
                // Previous button with text
                paginationHTML += `
                    <button class="btn btn-outline btn-sm pagination-btn" onclick="goToPage(${currentPage - 1})" ${currentPage <= 1 ? 'disabled style="opacity:0.5;cursor:not-allowed;"' : ''}>
                        Prev
                    </button>
                `;
                
                // Page numbers
                const maxVisiblePages = 5;
                let startPage = Math.max(1, currentPage - Math.floor(maxVisiblePages / 2));
                let endPage = Math.min(totalPages, startPage + maxVisiblePages - 1);
                
                if (endPage - startPage < maxVisiblePages - 1) {
                    startPage = Math.max(1, endPage - maxVisiblePages + 1);
                }
                
                if (startPage > 1) {
                    paginationHTML += `<button class="btn btn-outline btn-sm pagination-btn" onclick="goToPage(1)">1</button>`;
                    if (startPage > 2) {
                        paginationHTML += `<span style="color:var(--text-muted);padding:0 4px;">…</span>`;
                    }
                }
                
                for (let i = startPage; i <= endPage; i++) {
                    const isActive = i === currentPage;
                    paginationHTML += `
                        <button class="btn ${isActive ? 'btn-primary' : 'btn-outline'} btn-sm pagination-btn" onclick="goToPage(${i})" ${isActive ? 'style="font-weight:700;"' : ''}>
                            ${i}
                        </button>
                    `;
                }
                
                if (endPage < totalPages) {
                    if (endPage < totalPages - 1) {
                        paginationHTML += `<span style="color:var(--text-muted);padding:0 4px;">…</span>`;
                    }
                    paginationHTML += `<button class="btn btn-outline btn-sm pagination-btn" onclick="goToPage(${totalPages})">${totalPages}</button>`;
                }
                
                // Next button with text
                paginationHTML += `
                    <button class="btn btn-outline btn-sm pagination-btn" onclick="goToPage(${currentPage + 1})" ${currentPage >= totalPages ? 'disabled style="opacity:0.5;cursor:not-allowed;"' : ''}>
                        Next
                    </button>
                `;
                
                paginationHTML += '</div>';
                paginationContainer.innerHTML = paginationHTML;
            }
        } else {
            footer.style.display = 'none';
        }
    }
}

// Go to specific page
function goToPage(page) {
    const totalPages = Math.ceil(filteredEmployees.length / CARDS_PER_PAGE);
    if (page < 1 || page > totalPages) return;
    currentPage = page;
    renderEmployeeCards(filteredEmployees);
    
    // Scroll to grid
    const grid = document.getElementById('employeesGrid');
    if (grid) {
        setTimeout(() => {
            grid.scrollIntoView({ behavior: 'smooth', block: 'start' });
        }, 100);
    }
}

// Toggle DTR filter panel visibility (Select Employee card) - visible by
// default, toggled with the eye-slash/eye icon button.
function toggleDTRFilter() {
    const filterArea = document.getElementById('dtrFilterArea');
    const btn = document.getElementById('dtrHideFilterBtn');
    if (!filterArea) return;
    const isVisible = filterArea.style.display !== 'none';
    filterArea.style.display = isVisible ? 'none' : 'block';
    if (btn) {
        btn.innerHTML = isVisible
            ? '<i class="fa-solid fa-eye"></i> Show Filter'
            : '<i class="fa-solid fa-eye-slash"></i> Hide Filter';
    }
}

// Toggle filter bar visibility - same design/behavior as the DTR filter
// toggle above: visible by default, eye-slash/eye icon swap.
function toggleEmployeeFilter() {
    const filterBar = document.getElementById('employeeFilterBar');
    const btn = document.getElementById('employeeHideFilterBtn');
    if (filterBar) {
        const isVisible = filterBar.style.display !== 'none';
        filterBar.style.display = isVisible ? 'none' : 'block';
        
        // Update button text
        if (btn) {
            btn.innerHTML = isVisible
                ? '<i class="fa-solid fa-eye"></i> Show Filter'
                : '<i class="fa-solid fa-eye-slash"></i> Hide Filter';
        }
    }
}

// Filter employees based on search and dropdowns
function filterEmployees() {
    const searchInput = document.getElementById('employeeSearchInput');
    const roleFilter = document.getElementById('employeeRoleFilter');
    const deptFilter = document.getElementById('employeeDeptFilter');
    
    const searchTerm = searchInput ? searchInput.value.toLowerCase().trim() : '';
    const role = roleFilter ? roleFilter.value : 'all';
    const department = deptFilter ? deptFilter.value : 'all';
    
    filteredEmployees = allEmployees.filter(emp => {
        // Search filter
        let matchesSearch = true;
        if (searchTerm) {
            const fullname = `${emp.firstname || ''} ${emp.lastname || ''}`.toLowerCase();
            const employeeId = (emp.employeeid || '').toLowerCase();
            const email = (emp.email || '').toLowerCase();
            matchesSearch = fullname.includes(searchTerm) || 
                           employeeId.includes(searchTerm) || 
                           email.includes(searchTerm);
        }
        
        // Role filter
        let matchesRole = true;
        if (role !== 'all') {
            matchesRole = (emp.role || 'employee').toLowerCase() === role;
        }
        
        // Department filter
        let matchesDept = true;
        if (department !== 'all') {
            const empDept = (emp.department || '').toLowerCase();
            matchesDept = empDept === department.toLowerCase();
        }
        
        return matchesSearch && matchesRole && matchesDept;
    });
    
    // Reset to page 1 when filtering
    currentPage = 1;
    renderEmployeeCards(filteredEmployees);
}

// Re-apply the current filter inputs to `allEmployees` and rebuild
// `filteredEmployees` WITHOUT resetting the input elements or the page.
// Used by updateEmployeesGrid() on server refreshes so the grid doesn't
// blink or empty out every 5 seconds.
function applyCurrentEmployeeFilters() {
    const searchInput = document.getElementById('employeeSearchInput');
    const roleFilter = document.getElementById('employeeRoleFilter');
    const deptFilter = document.getElementById('employeeDeptFilter');
    
    const searchTerm = searchInput ? searchInput.value.toLowerCase().trim() : '';
    const role = roleFilter ? roleFilter.value : 'all';
    const department = deptFilter ? deptFilter.value : 'all';
    
    filteredEmployees = allEmployees.filter(emp => {
        // Search filter
        let matchesSearch = true;
        if (searchTerm) {
            const fullname = `${emp.firstname || ''} ${emp.lastname || ''}`.toLowerCase();
            const employeeId = (emp.employeeid || '').toLowerCase();
            const email = (emp.email || '').toLowerCase();
            matchesSearch = fullname.includes(searchTerm) || 
                           employeeId.includes(searchTerm) || 
                           email.includes(searchTerm);
        }
        
        // Role filter
        let matchesRole = true;
        if (role !== 'all') {
            matchesRole = (emp.role || 'employee').toLowerCase() === role;
        }
        
        // Department filter
        let matchesDept = true;
        if (department !== 'all') {
            const empDept = (emp.department || '').toLowerCase();
            matchesDept = empDept === department.toLowerCase();
        }
        
        return matchesSearch && matchesRole && matchesDept;
    });
    
    // Clamp currentPage so we never land on a page that no longer exists
    const totalPages = Math.ceil(filteredEmployees.length / CARDS_PER_PAGE) || 1;
    if (currentPage > totalPages) {
        currentPage = totalPages;
    }
}

// Clear all filters
function clearEmployeeFilters() {
    const searchInput = document.getElementById('employeeSearchInput');
    const roleFilter = document.getElementById('employeeRoleFilter');
    const deptFilter = document.getElementById('employeeDeptFilter');
    
    if (searchInput) searchInput.value = '';
    if (roleFilter) roleFilter.value = 'all';
    if (deptFilter) deptFilter.value = 'all';
    
    currentPage = 1;
    filteredEmployees = [...allEmployees];
    renderEmployeeCards(filteredEmployees);
}

// Employee Directory search dropdown state
let employeeSelectedSuggestionIndex = -1;

// Live-filters the employee grid as the user types AND shows a matching
// suggestions dropdown (same pattern/markup as the DTR employee search).
function searchEmployeesWithSuggestions(value) {
    // Keep the grid itself filtering live, same as before.
    filterEmployees();

    const dropdown = document.getElementById('employeeSearchDropdown');
    const clearBtn = document.getElementById('employeeSearchClear');
    if (!dropdown) return;

    const term = (value || '').trim().toLowerCase();
    if (clearBtn) clearBtn.style.display = term ? 'flex' : 'none';
    employeeSelectedSuggestionIndex = -1;

    if (!term) {
        dropdown.style.display = 'none';
        return;
    }

    const matches = allEmployees.filter(emp => {
        const fullname = `${emp.firstname || ''} ${emp.lastname || ''}`.toLowerCase();
        const employeeId = (emp.employeeid || '').toLowerCase();
        const email = (emp.email || '').toLowerCase();
        return fullname.includes(term) || employeeId.includes(term) || email.includes(term);
    }).slice(0, 8);

    dropdown.innerHTML = matches.length === 0
        ? '<div class="dtr-suggestion-empty">No employees found</div>'
        : matches.map((emp) => {
            const fullname = `${emp.firstname || ''} ${emp.lastname || ''}`.trim();
            const role = (emp.role || 'employee').toLowerCase();
            return `
                <div class="dtr-suggestion" onclick="selectEmployeeSuggestion('${escapeHtml(emp.uid)}')">
                    <div class="dtr-suggestion-avatar">${escapeHtml(initials(emp))}</div>
                    <div class="dtr-suggestion-body">
                        <div class="dtr-suggestion-name">${highlightMatchText(fullname, term)}</div>
                        <div class="dtr-suggestion-meta">${escapeHtml(emp.employeeid || 'N/A')}${emp.department ? ' &bull; ' + escapeHtml(emp.department) : ''}</div>
                    </div>
                    <span class="dtr-suggestion-badge role-${escapeHtml(role)}">${escapeHtml(role)}</span>
                </div>`;
        }).join('');

    dropdown.style.display = 'block';
}

// Picking a suggestion narrows the search box to that person and re-filters
function selectEmployeeSuggestion(uid) {
    const emp = getEmployeeByUid(uid);
    if (!emp) return;

    const input = document.getElementById('employeeSearchInput');
    const dropdown = document.getElementById('employeeSearchDropdown');
    const fullname = `${emp.firstname || ''} ${emp.lastname || ''}`.trim();

    if (input) input.value = fullname;
    if (dropdown) dropdown.style.display = 'none';
    filterEmployees();
}

// Clear the Employee Directory search box (role/department filters stay as-is)
function clearEmployeeSearch() {
    const input = document.getElementById('employeeSearchInput');
    const dropdown = document.getElementById('employeeSearchDropdown');
    const clearBtn = document.getElementById('employeeSearchClear');

    if (input) input.value = '';
    if (dropdown) dropdown.style.display = 'none';
    if (clearBtn) clearBtn.style.display = 'none';
    employeeSelectedSuggestionIndex = -1;
    filterEmployees();
}

// Keyboard navigation (arrows/enter/escape) for the Employee search dropdown
function handleEmployeeSearchKeydown(event) {
    const dropdown = document.getElementById('employeeSearchDropdown');
    if (!dropdown || dropdown.style.display === 'none') return;
    const items = dropdown.querySelectorAll('.dtr-suggestion');
    if (!items.length) return;

    if (event.key === 'ArrowDown') {
        event.preventDefault();
        employeeSelectedSuggestionIndex = Math.min(employeeSelectedSuggestionIndex + 1, items.length - 1);
    } else if (event.key === 'ArrowUp') {
        event.preventDefault();
        employeeSelectedSuggestionIndex = Math.max(employeeSelectedSuggestionIndex - 1, 0);
    } else if (event.key === 'Enter') {
        event.preventDefault();
        const target = employeeSelectedSuggestionIndex >= 0 ? items[employeeSelectedSuggestionIndex] : items[0];
        target.click();
        return;
    } else if (event.key === 'Escape') {
        dropdown.style.display = 'none';
        return;
    } else {
        return;
    }

    items.forEach(i => i.classList.remove('active'));
    items[employeeSelectedSuggestionIndex].classList.add('active');
    items[employeeSelectedSuggestionIndex].scrollIntoView({ block: 'nearest' });
}

// Hide the Employee search dropdown when clicking anywhere else on the page
document.addEventListener('click', (event) => {
    const input = document.getElementById('employeeSearchInput');
    const dropdown = document.getElementById('employeeSearchDropdown');
    if (dropdown && input && !input.contains(event.target) && !dropdown.contains(event.target)) {
        dropdown.style.display = 'none';
    }
});

// ============ END EMPLOYEE FUNCTIONS ============

// ============ VIEW EMPLOYEE MODAL ============

// Get employee by UID
function getEmployeeByUid(uid) {
    return allEmployees.find(emp => emp.uid === uid);
}

// View employee details in modal
function viewEmployee(uid) {
    const employee = getEmployeeByUid(uid);
    if (!employee) {
        showNotification('Employee not found.', 'error');
        return;
    }
    
    const fullname = `${employee.firstname || ''} ${employee.lastname || ''}`.trim() || 'Unknown';
    const role = employee.role || 'employee';
    const roleColor = role === 'admin' ? 'var(--danger)' : role === 'hr' ? 'var(--primary)' : 'var(--success)';
    const roleBg = role === 'admin' ? 'var(--danger-light)' : role === 'hr' ? 'var(--primary-light)' : 'var(--success-light)';
    
    // Build modal HTML
    const modalHtml = `
        <div class="modal-overlay" id="viewEmployeeModal" onclick="if(event.target===this) closeViewEmployeeModal()">
            <div class="modal-content view-modal" onclick="event.stopPropagation()">
                <div class="modal-header" style="background:linear-gradient(135deg, ${roleColor}, ${role === 'admin' ? '#DC2626' : role === 'hr' ? '#2563EB' : '#16A34A'});">
                    <div class="modal-avatar">${escapeHtml(fullname.split(' ').map(w => w[0] || '').join('').slice(0, 2).toUpperCase() || '--')}</div>
                    <div class="modal-user-info">
                        <h2>${escapeHtml(fullname)}</h2>
                        <span class="badge" style="background:${roleBg};color:${roleColor};font-size:12px;padding:4px 14px;">${escapeHtml(role.toUpperCase())}</span>
                    </div>
                    <button class="modal-close" onclick="closeViewEmployeeModal()">&times;</button>
                </div>
                <div class="modal-body">
                    <div class="detail-grid">
                        <div class="detail-item">
                            <span class="detail-label"><i class="fa-solid fa-id-badge"></i> Employee ID</span>
                            <span class="detail-value">${escapeHtml(employee.employeeid || employee.uid || 'N/A')}</span>
                        </div>
                        <div class="detail-item">
                            <span class="detail-label"><i class="fa-solid fa-envelope"></i> Email</span>
                            <span class="detail-value">${escapeHtml(employee.email || 'N/A')}</span>
                        </div>
                        <div class="detail-item">
                            <span class="detail-label"><i class="fa-solid fa-building"></i> Department</span>
                            <span class="detail-value">${escapeHtml(employee.department || 'N/A')}</span>
                        </div>
                        <div class="detail-item">
                            <span class="detail-label"><i class="fa-solid fa-briefcase"></i> Position</span>
                            <span class="detail-value">${escapeHtml(employee.position || 'N/A')}</span>
                        </div>
                        <div class="detail-item">
                            <span class="detail-label"><i class="fa-solid fa-phone"></i> Contact Number</span>
                            <span class="detail-value">${escapeHtml(employee.cpnumber || 'N/A')}</span>
                        </div>
                        <div class="detail-item">
                            <span class="detail-label"><i class="fa-solid fa-calendar"></i> Birth Date</span>
                            <span class="detail-value">${escapeHtml(employee.bdate || 'N/A')}</span>
                        </div>
                        <div class="detail-item">
                            <span class="detail-label"><i class="fa-solid fa-home"></i> Address</span>
                            <span class="detail-value">${escapeHtml(employee.address || 'N/A')}</span>
                        </div>
                        <div class="detail-item">
                            <span class="detail-label"><i class="fa-solid fa-user-tag"></i> Role</span>
                            <span class="detail-value"><span class="badge" style="background:${roleBg};color:${roleColor};">${escapeHtml(role.toUpperCase())}</span></span>
                        </div>
                        <div class="detail-item">
                            <span class="detail-label"><i class="fa-solid fa-id-card"></i> RFID</span>
                            <span class="detail-value"><code>${escapeHtml(employee.rfid || 'N/A')}</code></span>
                        </div>
                        <div class="detail-item">
                            <span class="detail-label"><i class="fa-solid fa-clock"></i> Registered</span>
                            <span class="detail-value">${employee.timestamp_creation ? new Date(employee.timestamp_creation).toLocaleString() : 'N/A'}</span>
                        </div>
                    </div>
                </div>
                <div class="modal-footer">
                    <button class="btn btn-outline" onclick="closeViewEmployeeModal()"><i class="fa-solid fa-times"></i> Close</button>
                    <button class="btn btn-primary" onclick="closeViewEmployeeModal(); editEmployee('${escapeHtml(employee.uid || '')}')"><i class="fa-solid fa-edit"></i> Edit</button>
                </div>
            </div>
        </div>
    `;
    
    // Remove existing modal if any
    const existingModal = document.getElementById('viewEmployeeModal');
    if (existingModal) existingModal.remove();
    
    // Add modal to body
    document.body.insertAdjacentHTML('beforeend', modalHtml);
    
    // Prevent body scroll
    document.body.style.overflow = 'hidden';
}

// Close view employee modal
function closeViewEmployeeModal() {
    const modal = document.getElementById('viewEmployeeModal');
    if (modal) modal.remove();
    document.body.style.overflow = '';
}

// ============ EDIT EMPLOYEE MODAL ============

// Edit employee details in modal
function editEmployee(uid) {
    const employee = getEmployeeByUid(uid);
    if (!employee) {
        showNotification('Employee not found.', 'error');
        return;
    }
    
    const fullname = `${employee.firstname || ''} ${employee.lastname || ''}`.trim() || 'Unknown';
    const role = employee.role || 'employee';
    const roleColor = role === 'admin' ? 'var(--danger)' : role === 'hr' ? 'var(--primary)' : 'var(--success)';
    
    // Build edit modal HTML
    const modalHtml = `
        <div class="modal-overlay" id="editEmployeeModal" onclick="if(event.target===this) closeEditEmployeeModal()">
            <div class="modal-content edit-modal" onclick="event.stopPropagation()">
                <div class="modal-header" style="background:linear-gradient(135deg, ${roleColor}, ${role === 'admin' ? '#DC2626' : role === 'hr' ? '#2563EB' : '#16A34A'});">
                    <div class="modal-avatar">${escapeHtml(fullname.split(' ').map(w => w[0] || '').join('').slice(0, 2).toUpperCase() || '--')}</div>
                    <div class="modal-user-info">
                        <h2>Edit Employee</h2>
                        <span style="font-size:13px;opacity:0.8;">${escapeHtml(fullname)}</span>
                    </div>
                    <button class="modal-close" onclick="closeEditEmployeeModal()">&times;</button>
                </div>
                <div class="modal-body">
                    <form id="editEmployeeForm" enctype="multipart/form-data" onsubmit="return submitEditEmployee(event)">
                        <div class="edit-grid">
                            <div class="form-group">
                                <label>Employee ID</label>
                                <input class="form-control" type="text" id="editEmployeeId" value="${escapeHtml(employee.employeeid || '')}" required />
                            </div>
                            <div class="form-group">
                                <label>RFID</label>
                                <input class="form-control" type="text" id="editRfid" value="${escapeHtml(employee.rfid || '')}" required maxlength="8" pattern="^[0-9A-Z]{8}$" placeholder="XXXXXXXX" />
                            </div>
                            <div class="form-group">
                                <label>First Name</label>
                                <input class="form-control" type="text" id="editFirstname" value="${escapeHtml(employee.firstname || '')}" required />
                            </div>
                            <div class="form-group">
                                <label>Last Name</label>
                                <input class="form-control" type="text" id="editLastname" value="${escapeHtml(employee.lastname || '')}" required />
                            </div>
                            <div class="form-group">
                                <label>Email</label>
                                <input class="form-control" type="email" id="editEmail" value="${escapeHtml(employee.email || '')}" required />
                            </div>
                            <div class="form-group">
                                <label>Contact Number</label>
                                <input class="form-control" type="text" id="editCpnumber" value="${escapeHtml(employee.cpnumber || '')}" required placeholder="+63XXXXXXXXXX" pattern="^\+63[0-9]{10,11}$" />
                            </div>
                            <div class="form-group">
                                <label>Address</label>
                                <input class="form-control" type="text" id="editAddress" value="${escapeHtml(employee.address || '')}" required />
                            </div>
                            <div class="form-group">
                                <label>Birth Date</label>
                                <input class="form-control" type="date" id="editBdate" value="${escapeHtml(employee.bdate || '')}" required />
                            </div>
                            <div class="form-group">
                                <label>Gender</label>
                                <select class="form-control" id="editGender">
                                    <option value="">-- Select Gender --</option>
                                    <option value="male" ${normalizeGender(employee.gender) === 'male' ? 'selected' : ''}>Male</option>
                                    <option value="female" ${normalizeGender(employee.gender) === 'female' ? 'selected' : ''}>Female</option>
                                    <option value="other" ${normalizeGender(employee.gender) === 'other' ? 'selected' : ''}>Other</option>
                                </select>
                            </div>
                            <div class="form-group">
                                <label>Department</label>
                                <select class="form-control" id="editDepartment">
                                    <option value="">-- Select Department --</option>
                                    <option value="cte" ${normalizeDepartment(employee.department) === 'cte' ? 'selected' : ''}>CTE • College of Teacher Education</option>
                                    <option value="cas" ${normalizeDepartment(employee.department) === 'cas' ? 'selected' : ''}>CAS • College of Arts and Sciences</option>
                                    <option value="cbhm" ${normalizeDepartment(employee.department) === 'cbhm' ? 'selected' : ''}>CBHM • College of Business Management and Hospitality Management</option>
                                    <option value="lhs" ${normalizeDepartment(employee.department) === 'lhs' ? 'selected' : ''}>LHS • Laboratory High School</option>
                                </select>
                            </div>
                            <div class="form-group">
                                <label>Position</label>
                                <select class="form-control" id="editPosition">
                                    <option value="">-- Select Position --</option>
                                    <option value="campus_director" ${normalizePosition(employee.position) === 'campus_director' ? 'selected' : ''}>Campus director</option>
                                    <option value="college_dean" ${normalizePosition(employee.position) === 'college_dean' ? 'selected' : ''}>College dean</option>
                                    <option value="high_school_principal" ${normalizePosition(employee.position) === 'high_school_principal' ? 'selected' : ''}>High school principal</option>
                                    <option value="program_chair" ${normalizePosition(employee.position) === 'program_chair' ? 'selected' : ''}>Program chair</option>
                                    <option value="professor" ${normalizePosition(employee.position) === 'professor' ? 'selected' : ''}>Professor</option>
                                    <option value="associate_professor" ${normalizePosition(employee.position) === 'associate_professor' ? 'selected' : ''}>Associate professor</option>
                                    <option value="assistant_professor" ${normalizePosition(employee.position) === 'assistant_professor' ? 'selected' : ''}>Assistant professor</option>
                                    <option value="instructor" ${normalizePosition(employee.position) === 'instructor' ? 'selected' : ''}>Instructor</option>
                                    <option value="part_time_lecturer" ${normalizePosition(employee.position) === 'part_time_lecturer' ? 'selected' : ''}>Part-time lecturer</option>
                                    <option value="administrative_officer" ${normalizePosition(employee.position) === 'administrative_officer' ? 'selected' : ''}>Administrative officer</option>
                                    <option value="campus_registrar" ${normalizePosition(employee.position) === 'campus_registrar' ? 'selected' : ''}>Campus registrar</option>
                                    <option value="administrative_aide" ${normalizePosition(employee.position) === 'administrative_aide' ? 'selected' : ''}>Administrative aide</option>
                                    <option value="guidance_counselor" ${normalizePosition(employee.position) === 'guidance_counselor' ? 'selected' : ''}>Guidance counselor</option>
                                    <option value="librarian" ${normalizePosition(employee.position) === 'librarian' ? 'selected' : ''}>Librarian</option>
                                </select>
                            </div>
                            <div class="form-group">
                                <label>Employment Type</label>
                                <select class="form-control" id="editEmploymentType">
                                    <option value="">-- Select Employment Type --</option>
                                    <option value="permanent_regular" ${normalizeEmploymentType(employee.employment_type) === 'permanent_regular' ? 'selected' : ''}>Permanent / Regular</option>
                                    <option value="job_order" ${normalizeEmploymentType(employee.employment_type) === 'job_order' ? 'selected' : ''}>Job Order (JO)</option>
                                    <option value="contract_service" ${normalizeEmploymentType(employee.employment_type) === 'contract_service' ? 'selected' : ''}>Contract of Service (COS)</option>
                                    <option value="provisional_temporary" ${normalizeEmploymentType(employee.employment_type) === 'provisional_temporary' ? 'selected' : ''}>Provisional / Temporary</option>
                                    <option value="substitute_casual" ${normalizeEmploymentType(employee.employment_type) === 'substitute_casual' ? 'selected' : ''}>Substitute / Casual</option>
                                </select>
                            </div>
                            <div class="form-group">
                                <label>Functional Role</label>
                                <select class="form-control" id="editFunctionalRole">
                                    <option value="">-- Select Functional Role --</option>
                                    <option value="teaching" ${normalizeFunctionalRole(employee.functional_role) === 'teaching' ? 'selected' : ''}>Teaching Personnel</option>
                                    <option value="administrative" ${normalizeFunctionalRole(employee.functional_role) === 'administrative' ? 'selected' : ''}>Administrative & Clerical Staff</option>
                                    <option value="student_services" ${normalizeFunctionalRole(employee.functional_role) === 'student_services' ? 'selected' : ''}>Student Services Personnel</option>
                                    <option value="facilities_security" ${normalizeFunctionalRole(employee.functional_role) === 'facilities_security' ? 'selected' : ''}>Facilities & Security Personnel</option>
                                </select>
                            </div>
                            <div class="form-group">
                                <label>Role</label>
                                <select class="form-control" id="editRole">
                                    <option value="employee" ${normalizeRole(employee.role) === 'employee' ? 'selected' : ''}>Employee</option>
                                    <option value="hr" ${normalizeRole(employee.role) === 'hr' ? 'selected' : ''}>HR</option>
                                    <option value="admin" ${normalizeRole(employee.role) === 'admin' ? 'selected' : ''}>Admin</option>
                                </select>
                            </div>
                            <div class="form-group">
                                <label>Username</label>
                                <input class="form-control" type="text" id="editUsername" value="${escapeHtml(employee.username || '')}" required />
                            </div>
                        </div>
                        <div class="form-group" style="margin-top:12px;">
                            <label>New Password (leave blank to keep current)</label>
                            <div class="password-wrap">
                                <input class="form-control" type="password" id="editPassword" placeholder="Enter new password (min. 8 characters)" minlength="8" />
                                <button type="button" class="password-toggle" onclick="togglePasswordVisibility('editPassword', this)">
                                    <i class="fa-solid fa-eye"></i>
                                </button>
                            </div>
                        </div>
                        <!-- Image upload field for editing -->
                        <div class="form-group" style="margin-top:12px;">
                            <label>Profile Image</label>
                            <input class="form-control" type="file" id="editImage" name="image" accept=".jpg,.jpeg,.png,.gif,.webp,image/*" />
                            <small style="color:var(--text-muted);font-size:11px;display:block;margin-top:4px;">Leave blank to keep current image. Upload new image to replace.</small>
                            <div id="editImagePreview" style="margin-top:8px;display:none;">
                                <img id="editImagePreviewImg" src="" alt="Preview" style="max-width:100px;max-height:100px;border-radius:8px;border:1px solid var(--border);padding:4px;" />
                                <button type="button" class="btn btn-outline btn-sm" onclick="document.getElementById('editImage').value='';document.getElementById('editImagePreview').style.display='none';" style="margin-left:8px;padding:2px 8px;font-size:11px;">Remove</button>
                            </div>
                        </div>
                        <input type="hidden" id="editUid" value="${escapeHtml(employee.uid || '')}" />
                        <input type="hidden" id="editRfidHidden" value="${escapeHtml(employee.rfid || '')}" />
                        <div id="editMessage" style="margin-top:10px;font-size:13px;display:none;"></div>
                        <div class="modal-footer" style="margin-top:16px;padding-top:16px;border-top:1px solid var(--border);">
                            <button type="button" class="btn btn-outline" onclick="closeEditEmployeeModal()"><i class="fa-solid fa-times"></i> Cancel</button>
                            <button type="submit" class="btn btn-primary"><i class="fa-solid fa-save"></i> Save Changes</button>
                        </div>
                    </form>
                </div>
            </div>
        </div>
    `;
    
    // Remove existing modal if any
    const existingModal = document.getElementById('editEmployeeModal');
    if (existingModal) existingModal.remove();
    
    // Add modal to body
    document.body.insertAdjacentHTML('beforeend', modalHtml);
    
    // Add image preview functionality
    const imageInput = document.getElementById('editImage');
    if (imageInput) {
        imageInput.addEventListener('change', function() {
            const previewDiv = document.getElementById('editImagePreview');
            const previewImg = document.getElementById('editImagePreviewImg');
            if (this.files && this.files[0]) {
                const reader = new FileReader();
                reader.onload = function(e) {
                    previewImg.src = e.target.result;
                    previewDiv.style.display = 'block';
                };
                reader.readAsDataURL(this.files[0]);
            } else {
                previewDiv.style.display = 'none';
            }
        });
    }
    
    // Prevent body scroll
    document.body.style.overflow = 'hidden';
}

// Close edit employee modal
function closeEditEmployeeModal() {
    const modal = document.getElementById('editEmployeeModal');
    if (modal) modal.remove();
    document.body.style.overflow = '';
}

// Submit edit employee form
async function submitEditEmployee(event) {
    event.preventDefault();

    const uid = document.getElementById('editUid').value;
    const rfid = document.getElementById('editRfidHidden').value;

    // Get form data - use FormData to handle file upload
    const formData = new FormData();
    formData.append('employeeid', document.getElementById('editEmployeeId').value);
    formData.append('rfid', document.getElementById('editRfid').value);
    formData.append('firstname', document.getElementById('editFirstname').value);
    formData.append('lastname', document.getElementById('editLastname').value);
    formData.append('email', document.getElementById('editEmail').value);

    // Guarantee the CP number always starts with +63 before sending.
    // The input may have a leading "0" or be missing the prefix entirely.
    let cpValue = (document.getElementById('editCpnumber').value || '').trim();
    if (cpValue && !cpValue.startsWith('+63')) {
        cpValue = '+63' + cpValue.replace(/^\+?6?3?/, '').replace(/^0+/, '');
    }
    formData.append('cpnumber', cpValue);

    formData.append('address', document.getElementById('editAddress').value);
    formData.append('bdate', document.getElementById('editBdate').value);
    formData.append('department', document.getElementById('editDepartment').value);
    formData.append('position', document.getElementById('editPosition').value);
    formData.append('employment_type', document.getElementById('editEmploymentType').value);
    formData.append('functional_role', document.getElementById('editFunctionalRole').value);
    formData.append('gender', document.getElementById('editGender').value);
    formData.append('role', document.getElementById('editRole').value);
    formData.append('username', document.getElementById('editUsername').value);

    const password = document.getElementById('editPassword').value;
    if (password) {
        // Enforce the same 8-character minimum as the employee self-service
        // password change. If the admin typed something shorter, show an
        // error and stop before sending anything to the server.
        if (password.length < 8) {
            const msgEl = document.getElementById('editMessage');
            msgEl.style.display = 'block';
            msgEl.style.color = '#EF4444';
            msgEl.innerHTML = '<i class="fa-solid fa-exclamation-circle"></i> New password must be at least 8 characters.';
            return;
        }
        formData.append('password', password);
    }

    // Get image file if selected
    const imageFile = document.getElementById('editImage').files[0];
    if (imageFile) {
        formData.append('image', imageFile);
    }

    const msgEl = document.getElementById('editMessage');
    msgEl.style.display = 'block';
    msgEl.style.color = '#3B82F6';
    msgEl.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i> Updating employee...';

    try {
        const response = await fetch(`${dashboardApiBaseUrl}/api/update-employee/${encodeURIComponent(rfid)}`, {
            method: 'PUT',
            headers: {
                'Authorization': `Bearer ${localStorage.getItem('tapinToken')}`
                // Do not set Content-Type for FormData - browser will set it with boundary
            },
            body: formData,
            credentials: 'include'
        });

        const result = await response.json();

        if (response.status === 401) {
            redirectToLogin();
            return;
        }

        if (!response.ok) {
            msgEl.style.color = '#EF4444';
            msgEl.innerHTML = `<i class="fa-solid fa-exclamation-circle"></i> ${result.message || 'Update failed.'}`;
            return;
        }

        // Additional validation: check that we got valid data back
        if (!result || !result.data || typeof result.data !== 'object') {
            msgEl.style.color = '#EF4444';
            msgEl.innerHTML = `<i class="fa-solid fa-exclamation-circle"></i> Invalid response from server.`;
            return;
        }

        const updatedEmp = result.data;
        // Additional check: make sure we got back an object that looks like an employee
        if (!updatedEmp.uid) {
            msgEl.style.color = '#EF4444';
            msgEl.innerHTML = `<i class="fa-solid fa-exclamation-circle"></i> Invalid employee data received.`;
            return;
        }

        msgEl.style.color = '#10B981';
        msgEl.innerHTML = '<i class="fa-solid fa-check-circle"></i> Employee updated successfully!';

        // Update the local data
        const index = allEmployees.findIndex(e => e.uid === uid);
        if (index !== -1) {
            allEmployees[index] = updatedEmp;
            // Re-sort allEmployees by uid after update
            allEmployees = sortEmployeesByUid(allEmployees);
            // Reapply filters instead of overwriting with all employees
            filterEmployees();
            // Store the FULL employee list (not the filtered subset) so the
            // next dashboard refresh sees the same data we already have and
            // does NOT reset the filter inputs / empty out the grid.
            previousEmployeesData = JSON.stringify(allEmployees);
            renderEmployeeCards(filteredEmployees);
            populateDepartmentFilter(allEmployees);

            // After updating, navigate to the page containing the edited
            // employee. Look it up in the FILTERED list so the page number
            // actually matches what's on screen.
            const updatedIndex = filteredEmployees.findIndex(e => e.uid === updatedEmp.uid);
            if (updatedIndex !== -1) {
                const newPage = Math.floor(updatedIndex / CARDS_PER_PAGE) + 1;
                if (newPage !== currentPage) {
                    currentPage = newPage;
                    renderEmployeeCards(filteredEmployees);
                }
            }
        }

        // Close modal after delay
        setTimeout(() => {
            closeEditEmployeeModal();
            showNotification('Employee updated successfully!', 'success');
        }, 1500);

    } catch (error) {
        console.error('Error updating employee:', error);
        msgEl.style.color = '#EF4444';
        msgEl.innerHTML = '<i class="fa-solid fa-exclamation-circle"></i> Network error. Please try again.';
    }
}

// Show notification
function showNotification(message, type = 'info') {
    const colors = {
        success: '#10B981',
        error: '#EF4444',
        warning: '#F59E0B',
        info: '#3B82F6'
    };
    
    const notification = document.createElement('div');
    notification.style.cssText = `
        position: fixed;
        bottom: 20px;
        right: 20px;
        padding: 12px 20px;
        background: ${colors[type] || colors.info};
        color: white;
        border-radius: 8px;
        font-size: 14px;
        font-weight: 500;
        box-shadow: 0 4px 12px rgba(0,0,0,0.15);
        z-index: 9999;
        max-width: 400px;
        transition: all 0.3s ease;
        opacity: 0;
        transform: translateY(20px);
    `;
    notification.textContent = message;
    document.body.appendChild(notification);
    
    setTimeout(() => {
        notification.style.opacity = '1';
        notification.style.transform = 'translateY(0)';
    }, 50);
    
    setTimeout(() => {
        notification.style.opacity = '0';
        notification.style.transform = 'translateY(20px)';
        setTimeout(() => {
            if (notification.parentNode) {
                notification.remove();
            }
        }, 300);
    }, 3000);
}

// Override the updateEmployeeTable function to use cards
function updateEmployeeTable(users) {
    // Use the new card-based display instead of table
    updateEmployeesGrid(users);
}

// ============ END EMPLOYEE FUNCTIONS ============

// ============ ATTENDANCE TABLE FUNCTIONS ============

// Store attendance data for pagination
let attendanceData = [];
let attendanceCurrentPage = 1;
const ATTENDANCE_PER_PAGE = 20;
let attendanceFilteredData = [];

// Update attendance table with pagination
function updateAttendanceTable(scans) {
    const body = document.getElementById('dashboardAttendanceBody');
    if (!body) return;
    
    // Store raw data for filtering
    attendanceData = scans || [];
    applyAttendanceFilters();
}

// Apply filters and render attendance table
function applyAttendanceFilters() {
    const deptFilter = document.getElementById('attendanceFilterDept');
    const statusFilter = document.getElementById('attendanceFilterStatus');
    
    const dept = deptFilter ? deptFilter.value : 'all';
    const status = statusFilter ? statusFilter.value : 'all';
    
    // Filter data
    attendanceFilteredData = attendanceData.filter(scan => {
        const employee = scan.employee;
        
        // Department filter
        let matchesDept = true;
        if (dept !== 'all') {
            const empDept = (employee && employee.department) || '';
            matchesDept = empDept.toLowerCase() === dept.toLowerCase();
        }
        
        // Status filter - only filter by present/absent/work_status
        let matchesStatus = true;
        if (status !== 'all') {
            const isPresent = employee ? true : false;
            const isOnWorkStatus = false; // We don't have work status in scan data
            
            if (status === 'present') {
                matchesStatus = isPresent;
            } else if (status === 'absent') {
                matchesStatus = !isPresent;
            } else if (status === 'work_status') {
                matchesStatus = isOnWorkStatus;
            }
        }
        
        return matchesDept && matchesStatus;
    });
    
    // Reset to page 1 when filtering
    attendanceCurrentPage = 1;
    renderAttendanceTable();
}

// Render attendance table with pagination
function renderAttendanceTable() {
    const body = document.getElementById('dashboardAttendanceBody');
    const paginationContainer = document.getElementById('attendancePagination');
    const countDisplay = document.getElementById('attendanceCountDisplay');
    
    if (!body) return;
    
    // Calculate pagination
    const totalItems = attendanceFilteredData.length;
    const totalPages = Math.ceil(totalItems / ATTENDANCE_PER_PAGE);
    const startIndex = (attendanceCurrentPage - 1) * ATTENDANCE_PER_PAGE;
    const endIndex = Math.min(startIndex + ATTENDANCE_PER_PAGE, totalItems);
    const pageData = attendanceFilteredData.slice(startIndex, endIndex);
    
    if (pageData.length === 0) {
        body.innerHTML = `<tr><td colspan="10" style="text-align:center;padding:30px;color:var(--text-muted);">
            <i class="fa-solid fa-info-circle" style="font-size:20px;display:block;margin-bottom:10px;"></i>
            No attendance records found.
        </td></tr>`;
        if (paginationContainer) paginationContainer.innerHTML = '';
        if (countDisplay) countDisplay.textContent = '';
        return;
    }
    
    // Generate table rows
    body.innerHTML = pageData.map((scan) => {
        const employee = scan.employee;
        const name = employee ? `${employee.firstname || ''} ${employee.lastname || ''}`.trim() : 'Unknown card';
        const role = employee ? employee.role || 'Unregistered' : 'Unregistered';
        const department = employee ? employee.department || '--' : '--';
        const position = employee ? employee.position || '--' : '--';
        const rfid = scan.rfid || '--';
        const scannedAt = scan.scanned_at || '--';
        const isPresent = employee ? true : false;
        
        return `<tr>
            <td><strong>${escapeHtml(employee ? employee.employeeid || employee.uid || '--' : '--')}</strong></td>
            <td>${escapeHtml(name)}</td>
            <td>${escapeHtml(department)}</td>
            <td>${escapeHtml(position)}</td>
            <td><code>${escapeHtml(rfid)}</code></td>
            <td>${escapeHtml(scannedAt)}</td>
            <td>--</td>
            <td><span class="badge ${isPresent ? 'badge-present' : 'badge-absent'}"><span class="badge-dot"></span>${isPresent ? 'Present' : 'Unknown'}</span></td>
            <td>RFID device</td>
            <td>Live scan</td>
        </tr>`;
    }).join('');
    
    // Update count display (above pagination)
    if (countDisplay) {
        if (totalItems > 0) {
            countDisplay.textContent = `Showing ${startIndex + 1} - ${endIndex} of ${totalItems}`;
        } else {
            countDisplay.textContent = '';
        }
    }
    
    // Generate pagination controls - Updated with Prev/Next text
    if (paginationContainer) {
        if (totalPages > 1) {
            let paginationHTML = '<div class="pagination-controls" style="display:flex;gap:6px;justify-content:center;align-items:center;flex-wrap:wrap;margin-top:8px;">';
            
            // Previous button with text
            paginationHTML += `
                <button class="btn btn-outline btn-sm pagination-btn" onclick="goToAttendancePage(${attendanceCurrentPage - 1})" ${attendanceCurrentPage <= 1 ? 'disabled style="opacity:0.5;cursor:not-allowed;"' : ''}>
                    Prev
                </button>
            `;
            
            // Page numbers
            const maxVisiblePages = 5;
            let startPage = Math.max(1, attendanceCurrentPage - Math.floor(maxVisiblePages / 2));
            let endPage = Math.min(totalPages, startPage + maxVisiblePages - 1);
            
            if (endPage - startPage < maxVisiblePages - 1) {
                startPage = Math.max(1, endPage - maxVisiblePages + 1);
            }
            
            if (startPage > 1) {
                paginationHTML += `<button class="btn btn-outline btn-sm pagination-btn" onclick="goToAttendancePage(1)">1</button>`;
                if (startPage > 2) {
                    paginationHTML += `<span style="color:var(--text-muted);padding:0 4px;">…</span>`;
                }
            }
            
            for (let i = startPage; i <= endPage; i++) {
                const isActive = i === attendanceCurrentPage;
                paginationHTML += `
                    <button class="btn ${isActive ? 'btn-primary' : 'btn-outline'} btn-sm pagination-btn" onclick="goToAttendancePage(${i})" ${isActive ? 'style="font-weight:700;"' : ''}>
                        ${i}
                    </button>
                `;
            }
            
            if (endPage < totalPages) {
                if (endPage < totalPages - 1) {
                    paginationHTML += `<span style="color:var(--text-muted);padding:0 4px;">…</span>`;
                }
                paginationHTML += `<button class="btn btn-outline btn-sm pagination-btn" onclick="goToAttendancePage(${totalPages})">${totalPages}</button>`;
            }
            
            // Next button with text
            paginationHTML += `
                <button class="btn btn-outline btn-sm pagination-btn" onclick="goToAttendancePage(${attendanceCurrentPage + 1})" ${attendanceCurrentPage >= totalPages ? 'disabled style="opacity:0.5;cursor:not-allowed;"' : ''}>
                    Next
                </button>
            `;
            
            paginationHTML += '</div>';
            paginationContainer.innerHTML = paginationHTML;
        } else {
            // No pagination buttons needed, just clear the container
            paginationContainer.innerHTML = '';
        }
    }
}

// Go to specific attendance page
function goToAttendancePage(page) {
    const totalPages = Math.ceil(attendanceFilteredData.length / ATTENDANCE_PER_PAGE);
    if (page < 1 || page > totalPages) return;
    attendanceCurrentPage = page;
    renderAttendanceTable();
    
    // Scroll to table
    const table = document.querySelector('#realtime .card');
    if (table) {
        setTimeout(() => {
            table.scrollIntoView({ behavior: 'smooth', block: 'start' });
        }, 100);
    }
}

// Populate department filter with unique departments
function populateAttendanceDepartmentFilter(scans) {
    const deptFilter = document.getElementById('attendanceFilterDept');
    if (!deptFilter) return;
    
    // Get unique departments from employee data
    const departments = new Set();
    scans.forEach(scan => {
        if (scan.employee && scan.employee.department) {
            departments.add(scan.employee.department);
        }
    });
    
    // Clear existing options except the first one
    while (deptFilter.options.length > 1) {
        deptFilter.remove(1);
    }
    
    // Sort departments alphabetically
    const sortedDepts = Array.from(departments).sort();
    
    // Add departments to dropdown
    sortedDepts.forEach(dept => {
        if (dept && dept.trim()) {
            const option = document.createElement('option');
            option.value = dept;
            option.textContent = dept;
            deptFilter.appendChild(option);
        }
    });
}

// ============ END ATTENDANCE TABLE FUNCTIONS ============

// ============ ORIGINAL updateScanTable (kept for backward compatibility) ============
function updateScanTable(scans) {
    const body = document.getElementById('dashboardScanBody');
    if (!body) return;
    body.innerHTML = scans.slice(0, 10).map((scan) => {
        const employee = scan.employee;
        const name = employee ? `${employee.firstname || ''} ${employee.lastname || ''}`.trim() : 'Unknown card';
        return `<tr>
            <td><div class="emp-cell"><div class="emp-avatar">${escapeHtml(employee ? initials(employee) : '--')}</div><div><div class="emp-name">${escapeHtml(name)}</div><div class="emp-id">${escapeHtml(scan.rfid)}</div></div></div></td>
            <td>${escapeHtml(employee ? employee.role : 'Unregistered')}</td><td>${escapeHtml(scan.scanned_at)}</td><td>--</td>
            <td><span class="badge ${employee ? 'badge-present' : 'badge-absent'}"><span class="badge-dot"></span>${employee ? 'Recognized' : 'Unknown'}</span></td>
        </tr>`;
    }).join('') || '<tr><td colspan="5">No RFID scans received today.</td></tr>';
}

// Update the activity timeline with data from the activity feed
function updateActivityTimeline(activities) {
    const timeline = document.getElementById('dashboardTimeline');
    if (!timeline) return;
    
    if (!activities || activities.length === 0) {
        timeline.innerHTML = `
            <div style="display:flex;justify-content:center;align-items:center;padding:30px 0;color:var(--text-muted);font-size:13px;">
                <i class="fa-solid fa-info-circle" style="margin-right:8px;"></i> No recent activities
            </div>
        `;
        return;
    }
    
    // Show latest 15 activities for better visibility
    const displayActivities = activities.slice(0, 15);
    
    timeline.innerHTML = displayActivities.map((activity) => {
        const timestamp = new Date(activity.timestamp);
        const timeStr = timestamp.toLocaleTimeString(undefined, {
            hour: '2-digit',
            minute: '2-digit'
        });
        const dateStr = timestamp.toLocaleDateString(undefined, {
            month: 'short',
            day: 'numeric'
        });
        
        // Determine icon and color based on activity type
        let icon = 'fa-solid fa-circle-info';
        let color = 'var(--primary)';
        let bgColor = 'var(--primary-light)';
        
        switch (activity.type) {
            case 'attendance':
                if (activity.action === 'attendance_time_in') {
                    icon = 'fa-solid fa-sign-in-alt';
                    color = 'var(--success)';
                    bgColor = 'var(--success-light)';
                } else if (activity.action === 'attendance_time_out') {
                    icon = 'fa-solid fa-sign-out-alt';
                    color = 'var(--warning)';
                    bgColor = 'var(--warning-light)';
                } else {
                    icon = 'fa-solid fa-clock';
                    color = 'var(--primary)';
                    bgColor = 'var(--primary-light)';
                }
                break;
            case 'work_status':
                if (activity.action === 'work_status_approved') {
                    icon = 'fa-solid fa-check-circle';
                    color = 'var(--success)';
                    bgColor = 'var(--success-light)';
                } else if (activity.action === 'work_status_rejected') {
                    icon = 'fa-solid fa-times-circle';
                    color = 'var(--danger)';
                    bgColor = 'var(--danger-light)';
                } else {
                    icon = 'fa-solid fa-briefcase';
                    color = 'var(--leave)';
                    bgColor = 'var(--leave-light)';
                }
                break;
            case 'employee':
                if (activity.action === 'employee_registered') {
                    icon = 'fa-solid fa-user-plus';
                    color = 'var(--success)';
                    bgColor = 'var(--success-light)';
                } else {
                    icon = 'fa-solid fa-user-edit';
                    color = 'var(--primary)';
                    bgColor = 'var(--primary-light)';
                }
                break;
            case 'system':
                if (activity.action === 'user_login') {
                    icon = 'fa-solid fa-sign-in-alt';
                    color = 'var(--primary)';
                    bgColor = 'var(--primary-light)';
                } else if (activity.action === 'user_logout') {
                    icon = 'fa-solid fa-sign-out-alt';
                    color = 'var(--warning)';
                    bgColor = 'var(--warning-light)';
                } else {
                    icon = 'fa-solid fa-server';
                    color = 'var(--accent)';
                    bgColor = 'var(--accent-light)';
                }
                break;
            default:
                icon = 'fa-solid fa-circle-info';
                color = 'var(--primary)';
                bgColor = 'var(--primary-light)';
        }
        
        // Get user info for display
        let userDisplay = '';
        if (activity.user && activity.user.name) {
            userDisplay = `<span class="timeline-user">${escapeHtml(activity.user.name)}</span>`;
        }
        
        return `
            <div class="timeline-item">
                <div class="timeline-icon" style="background:${bgColor};color:${color};">
                    <i class="${icon}"></i>
                </div>
                <div class="timeline-content">
                    <div class="timeline-text">${escapeHtml(activity.details)}</div>
                    <div class="timeline-meta">
                        <span>${escapeHtml(dateStr)} ${escapeHtml(timeStr)}</span>
                        ${userDisplay ? `&nbsp;·&nbsp;${userDisplay}` : ''}
                        <span class="timeline-badge" style="background:${bgColor};color:${color};">
                            ${escapeHtml(activity.type)}
                        </span>
                    </div>
                </div>
            </div>
        `;
    }).join('');
}

// Fetch activity feed data
async function loadActivityFeed() {
    try {
        const response = await fetch(`${dashboardApiBaseUrl}/api/activity-feed?limit=50`, {
            method: 'GET',
            headers: getAuthHeaders(),
            credentials: 'include',
            cache: 'no-store'
        });
        
        if (response.status === 401) {
            return;
        }
        
        if (!response.ok) {
            console.error('Failed to load activity feed:', response.status);
            return;
        }
        
        const result = await response.json();
        if (result.status === 'success' && result.data) {
            updateActivityTimeline(result.data.activities);
        }
    } catch (error) {
        console.error('Error loading activity feed:', error);
    }
}

// ============ DAILY STATS FETCHER (PERSISTENT PRESENT + SCAN COUNTERS) ============
// The server keeps track of these numbers in storage/database/daily_stats.json.
// They are written on EVERY RFID tap and face scan, regardless of whether any
// dashboard is open. This lightweight endpoint lets us refresh just those
// numbers frequently without re-downloading the full dashboard payload.
//
// The server now keeps THREE separate counters:
//   • present_count    — unique employees who tapped in today
//   • rfid_scan_count  — RFID taps only
//   • face_scan_count  — facial-recognition matches only
//   • scan_count       — total (RFID + face combined)

async function loadDailyStats() {
    try {
        const response = await fetch(`${dashboardApiBaseUrl}/api/daily-stats`, {
            method: 'GET',
            headers: getAuthHeaders(),
            credentials: 'include',
            cache: 'no-store'
        });

        if (response.status === 401) {
            return;
        }
        if (!response.ok) {
            console.error('Failed to load daily stats:', response.status);
            return;
        }

        const result = await response.json();
        if (result.status === 'success' && result.data) {
            applyDailyStatsToCards(result.data);
        }
    } catch (error) {
        console.error('Error loading daily stats:', error);
    }
}

// Update ONLY the count-based cards with the numbers returned by the server:
//   • #statPresentToday  — from present_count
//   • #statRfidScans     — from rfid_scan_count (RFID taps only)
//   • #statFaceScans     — from face_scan_count (facial matches only)
//
// Everything else on the dashboard continues to come from
// /api/dashboard-data.
function applyDailyStatsToCards(dailyStats) {
    const presentEl = document.getElementById('statPresentToday');
    if (presentEl) presentEl.textContent = dailyStats.present_count ?? 0;

    // RFID Scans card — RFID-only counter. Falls back to the combined
    // scan_count if the server for some reason didn't send rfid_scan_count
    // (keeps older backends working without error).
    const rfidEl = document.getElementById('statRfidScans');
    if (rfidEl) {
        const rfidValue = (dailyStats.rfid_scan_count !== undefined)
            ? dailyStats.rfid_scan_count
            : (dailyStats.scan_count ?? 0);
        rfidEl.textContent = rfidValue;
    }

    // Facial Scans card — face-only counter.
    const faceEl = document.getElementById('statFaceScans');
    if (faceEl) {
        faceEl.textContent = dailyStats.face_scan_count ?? 0;
    }

    // Refresh the small progress bars as well, since they depend on these
    // numbers. We re-use the existing bar-update helper by feeding it a
    // minimal stats object — the rest of the fields stay whatever the last
    // /api/dashboard-data call set.
    updateStatProgressBars({
        total_employees: (window.__lastStats && window.__lastStats.total_employees) || 1,
        present_today: dailyStats.present_count ?? 0,
        absent_today: (window.__lastStats && window.__lastStats.absent_today) || 0,
        on_work_status: (window.__lastStats && window.__lastStats.on_work_status) || 0,
        attendance_rate: (window.__lastStats && window.__lastStats.attendance_rate) || 0,
        rfid_scans_today: (dailyStats.rfid_scan_count !== undefined)
            ? dailyStats.rfid_scan_count
            : (dailyStats.scan_count ?? 0),
        face_scans_today: dailyStats.face_scan_count ?? 0,
    });

    // Remember the last-known values so the next call can fill in the gaps.
    window.__lastDailyStats = dailyStats;
}

// ============ END DAILY STATS FETCHER ============

// ============ DTR FUNCTIONS ============

// Employees available for the DTR search box (populated below)
let dtrEmployeesList = [];
let dtrSelectedSuggestionIndex = -1;

// Load DTR employees for the search box
async function loadDTREmployees() {
    try {
        const response = await fetch(`${dashboardApiBaseUrl}/api/dtr/employees`, {
            method: 'GET',
            headers: getAuthHeaders(),
            credentials: 'include',
            cache: 'no-store'
        });
        
        if (response.status === 401) {
            redirectToLogin();
            return;
        }
        
        if (!response.ok) {
            console.error('Failed to load DTR employees:', response.status);
            return;
        }
        
        const result = await response.json();
        if (result.status === 'success' && result.data) {
            dtrEmployeesList = result.data || [];

            // Auto-load first employee if available
            // Commented out to provide clean search input as requested
            // if (dtrEmployeesList.length > 0) {
            //     selectDTREmployee(dtrEmployeesList[0].rfid);
            // }

            // Clear search input to provide clean input as requested
            const dtrSearchInput = document.getElementById('dtrEmployeeSearch');
            if (dtrSearchInput) {
                dtrSearchInput.value = '';
            }

            // Also clear the hidden selection input
            const dtrHiddenSelect = document.getElementById('dtrEmployeeSelect');
            if (dtrHiddenSelect) {
                dtrHiddenSelect.value = '';
            }

            // Hide dropdown and clear button for clean state
            const dtrDropdown = document.getElementById('dtrEmployeeDropdown');
            if (dtrDropdown) {
                dtrDropdown.style.display = 'none';
            }
            const dtrClearBtn = document.getElementById('dtrSearchClear');
            if (dtrClearBtn) {
                dtrClearBtn.style.display = 'none';
            }

            // Reset selection index
            dtrSelectedSuggestionIndex = -1;
        }
    } catch (error) {
        console.error('Error loading DTR employees:', error);
    }
}

// Build the initials shown on a DTR search suggestion's avatar circle
function initialsFromFullname(fullname) {
    return String(fullname || '').trim().split(/\s+/).map(p => p[0] || '').join('').slice(0, 2).toUpperCase() || '--';
}

// Wrap the matched portion of a suggestion's name in a highlight span
function highlightMatchText(text, term) {
    const safeText = escapeHtml(text || '');
    if (!term) return safeText;
    const idx = safeText.toLowerCase().indexOf(term.toLowerCase());
    if (idx === -1) return safeText;
    return safeText.slice(0, idx)
        + '<span class="dtr-suggestion-highlight">' + safeText.slice(idx, idx + term.length) + '</span>'
        + safeText.slice(idx + term.length);
}

// Filter the DTR employee list as the user types and show matching
// suggestions in the dropdown (mirrors the Employee Directory search).
function filterDTREmployees() {
    const input = document.getElementById('dtrEmployeeSearch');
    const dropdown = document.getElementById('dtrEmployeeDropdown');
    const clearBtn = document.getElementById('dtrSearchClear');
    if (!input || !dropdown) return;

    const term = input.value.trim().toLowerCase();
    if (clearBtn) clearBtn.style.display = term ? 'flex' : 'none';
    dtrSelectedSuggestionIndex = -1;

    const matches = (term
        ? dtrEmployeesList.filter(emp => {
            const fullname = (emp.fullname || '').toLowerCase();
            const employeeId = (emp.employeeid || '').toLowerCase();
            return fullname.includes(term) || employeeId.includes(term);
        })
        : dtrEmployeesList
    ).slice(0, 8);

    dropdown.innerHTML = matches.length === 0
        ? '<div class="dtr-suggestion-empty">No employees found</div>'
        : matches.map((emp) => {
            const role = (emp.role || 'employee').toLowerCase();
            return `
                <div class="dtr-suggestion" onclick="selectDTREmployee('${escapeHtml(emp.rfid)}')">
                    <div class="dtr-suggestion-avatar">${initialsFromFullname(emp.fullname)}</div>
                    <div class="dtr-suggestion-body">
                        <div class="dtr-suggestion-name">${highlightMatchText(emp.fullname, term)}</div>
                        <div class="dtr-suggestion-meta">${escapeHtml(emp.employeeid || 'N/A')}${emp.department ? ' &bull; ' + escapeHtml(emp.department) : ''}</div>
                    </div>
                    <span class="dtr-suggestion-badge role-${escapeHtml(role)}">${escapeHtml(role)}</span>
                </div>`;
        }).join('');

    dropdown.style.display = 'block';
}

// Pick an employee from the DTR search dropdown (or auto-select on load)
function selectDTREmployee(rfid) {
    const emp = dtrEmployeesList.find(e => e.rfid === rfid);
    if (!emp) return;

    const input = document.getElementById('dtrEmployeeSearch');
    const hidden = document.getElementById('dtrEmployeeSelect');
    const dropdown = document.getElementById('dtrEmployeeDropdown');
    const clearBtn = document.getElementById('dtrSearchClear');

    if (input) input.value = `${emp.fullname} (${emp.employeeid || 'N/A'})`;
    if (hidden) hidden.value = emp.rfid;
    if (dropdown) dropdown.style.display = 'none';
    if (clearBtn) clearBtn.style.display = 'flex';

    // Auto-load the DTR once an employee is picked
    loadDTRRecord();
}

// Clear the DTR employee search box
function clearDTRSearch() {
    const input = document.getElementById('dtrEmployeeSearch');
    const hidden = document.getElementById('dtrEmployeeSelect');
    const dropdown = document.getElementById('dtrEmployeeDropdown');
    const clearBtn = document.getElementById('dtrSearchClear');

    if (input) input.value = '';
    if (hidden) hidden.value = '';
    if (dropdown) dropdown.style.display = 'none';
    if (clearBtn) clearBtn.style.display = 'none';
    dtrSelectedSuggestionIndex = -1;
}

// Keyboard navigation (arrows/enter/escape) for the DTR search dropdown
function handleDTRSearchKeydown(event) {
    const dropdown = document.getElementById('dtrEmployeeDropdown');
    if (!dropdown || dropdown.style.display === 'none') return;
    const items = dropdown.querySelectorAll('.dtr-suggestion');
    if (!items.length) return;

    if (event.key === 'ArrowDown') {
        event.preventDefault();
        dtrSelectedSuggestionIndex = Math.min(dtrSelectedSuggestionIndex + 1, items.length - 1);
    } else if (event.key === 'ArrowUp') {
        event.preventDefault();
        dtrSelectedSuggestionIndex = Math.max(dtrSelectedSuggestionIndex - 1, 0);
    } else if (event.key === 'Enter') {
        event.preventDefault();
        const target = dtrSelectedSuggestionIndex >= 0 ? items[dtrSelectedSuggestionIndex] : items[0];
        target.click();
        return;
    } else if (event.key === 'Escape') {
        dropdown.style.display = 'none';
        return;
    } else {
        return;
    }

    items.forEach(i => i.classList.remove('active'));
    items[dtrSelectedSuggestionIndex].classList.add('active');
    items[dtrSelectedSuggestionIndex].scrollIntoView({ block: 'nearest' });
}

// Hide the DTR suggestion dropdown when clicking anywhere else on the page
document.addEventListener('click', (event) => {
    const input = document.getElementById('dtrEmployeeSearch');
    const dropdown = document.getElementById('dtrEmployeeDropdown');
    if (dropdown && input && !input.contains(event.target) && !dropdown.contains(event.target)) {
        dropdown.style.display = 'none';
    }
});

// Load available months for DTR
async function loadDTRMonths() {
    try {
        const response = await fetch(`${dashboardApiBaseUrl}/api/dtr/months`, {
            method: 'GET',
            headers: getAuthHeaders(),
            credentials: 'include',
            cache: 'no-store'
        });
        
        if (response.status === 401) {
            redirectToLogin();
            return;
        }
        
        if (!response.ok) {
            console.error('Failed to load DTR months:', response.status);
            return;
        }
        
        const result = await response.json();
        if (result.status === 'success' && result.data) {
            const select = document.getElementById('dtrMonthSelect');
            if (!select) return;
            
            // Clear existing options except the first one
            while (select.options.length > 1) {
                select.remove(1);
            }
            
            // Add months to dropdown
            result.data.forEach(month => {
                const option = document.createElement('option');
                option.value = month.value;
                option.textContent = month.label;
                select.appendChild(option);
            });
            
            // Set current month as default if available
            const currentMonth = new Date().toISOString().slice(0, 7);
            for (let i = 0; i < select.options.length; i++) {
                if (select.options[i].value === currentMonth) {
                    select.selectedIndex = i;
                    break;
                }
            }
        }
    } catch (error) {
        console.error('Error loading DTR months:', error);
    }
}

// Resolve a complete, reliable employee info object for the DTR by combining
// whatever the /api/dtr/record endpoint returned (apiEmployee) with the info
// already known from the employee dropdown (populated by loadDTREmployees).
// This is what stops the printed/PDF name from ever showing "Unknown" —
// the dropdown always has fullname/position/department even if the record
// endpoint's employee object is incomplete.
function resolveDTREmployeeInfo(select, apiEmployee, apiRecord) {
    apiEmployee = apiEmployee || {};
    const rfid = select && select.value;
    const fromList = rfid ? (dtrEmployeesList.find(e => e.rfid === rfid) || {}) : {};

    const apiFullname = apiEmployee.fullname
        || (apiEmployee.lastname ? `${apiEmployee.lastname}, ${apiEmployee.firstname || ''}`.trim() : '');

    return {
        fullname: apiFullname || fromList.fullname || 'Unknown',
        employeeid: apiEmployee.employeeid || fromList.employeeid || (apiRecord && apiRecord.employee_id) || '',
        position: apiEmployee.position || fromList.position || '',
        department: apiEmployee.department || fromList.department || '',
        role: apiEmployee.role || fromList.role || 'employee',
        regularTime: apiEmployee.regular_time || apiEmployee.regularTime || 'DEFAULT'
    };
}

// Render the on-screen DTR employee info panel — Name / Position / Department /
// Regular Time, each on its own line with aligned labels, same fields shown
// on the printed form. Creates the panel above the DTR table the first time
// it's needed, then just updates its content on every subsequent load.
function renderDTREmployeeInfoPanel(info) {
    let panel = document.getElementById('dtrEmployeeInfoPanel');

    if (!panel) {
        panel = document.createElement('div');
        panel.id = 'dtrEmployeeInfoPanel';
        panel.className = 'dtr-employee-info-panel';

        const tbody = document.getElementById('dtrTableBody');
        const table = tbody ? tbody.closest('table') : null;
        if (table && table.parentNode) {
            table.parentNode.insertBefore(panel, table);
        } else {
            const nameEl = document.getElementById('dtrEmployeeName');
            const anchor = nameEl ? nameEl.closest('div') : null;
            if (anchor && anchor.parentNode) {
                anchor.parentNode.insertBefore(panel, anchor.nextSibling);
            }
        }
    }

    panel.innerHTML = `
        <div class="dtr-info-row"><span class="dtr-info-label">Name:</span><span class="dtr-info-value">${escapeHtml(info.fullname || '--')}</span></div>
        <div class="dtr-info-row"><span class="dtr-info-label">Position:</span><span class="dtr-info-value">${escapeHtml(info.position || '--')}</span></div>
        <div class="dtr-info-row"><span class="dtr-info-label">Department:</span><span class="dtr-info-value">${escapeHtml(info.department || '--')}</span></div>
        <div class="dtr-info-row"><span class="dtr-info-label">Regular Time:</span><span class="dtr-info-value">${escapeHtml(info.regularTime || 'DEFAULT')}</span></div>
    `;

    if (!document.getElementById('dtrEmployeeInfoPanelStyles')) {
        const style = document.createElement('style');
        style.id = 'dtrEmployeeInfoPanelStyles';
        style.textContent = `
            .dtr-employee-info-panel {
                display: flex;
                flex-direction: column;
                gap: 4px;
                font-size: 13px;
                margin: 10px 0 16px 0;
                padding: 10px 14px;
                border: 1px solid var(--border, #e5e7eb);
                border-radius: 8px;
                background: var(--card-bg, #ffffff);
            }
            .dtr-employee-info-panel .dtr-info-row {
                display: flex;
                align-items: baseline;
            }
            .dtr-employee-info-panel .dtr-info-label {
                font-weight: 700;
                width: 120px;
                flex-shrink: 0;
            }
            .dtr-employee-info-panel .dtr-info-value {
                font-weight: 500;
            }
        `;
        document.head.appendChild(style);
    }
}

// Load DTR record for selected employee
async function loadDTRRecord() {
    const select = document.getElementById('dtrEmployeeSelect');
    const monthSelect = document.getElementById('dtrMonthSelect');
    if (!select || !monthSelect) {
        console.error('loadDTRRecord: missing #dtrEmployeeSelect or #dtrMonthSelect in the page.');
        return;
    }
    const rfid = select.value;
    const month = monthSelect.value;
    
    if (!rfid || rfid === '') {
        showDTRMessage('Please select an employee.', 'warning');
        return;
    }
    
    if (!month || month === '') {
        showDTRMessage('Please select a month.', 'warning');
        return;
    }
    
    // Get employee info from the DTR employees list
    const emp = dtrEmployeesList.find(e => e.rfid === rfid) || {};
    const fullname = emp.fullname || '';
    const employeeid = emp.employeeid || '';
    const position = emp.position || '';
    const department = emp.department || '';
    const role = emp.role || 'employee';
    
    // Update employee info display - simplified (only signature; the
    // Employee/Employee ID/Role/Month block was removed)
    const sigEl = document.getElementById('dtrSigEmployee');
    if (sigEl) sigEl.textContent = fullname || 'Employee Signature';

    // Show the full Name / Position / Department / Regular Time block,
    // aligned the same way it appears on the printed DTR.
    renderDTREmployeeInfoPanel({
        fullname: fullname || 'Unknown',
        position,
        department,
        regularTime: 'DEFAULT'
    });
    
    try {
        // Show loading state
        const tbody = document.getElementById('dtrTableBody');
        if (tbody) {
            tbody.innerHTML = `<tr><td colspan="10" style="text-align:center;padding:20px;color:var(--text-muted);">
                <i class="fa-solid fa-spinner fa-spin" style="margin-right:8px;"></i> Loading DTR...
            </td></tr>`;
        }

        const response = await fetch(`${dashboardApiBaseUrl}/api/dtr/record/${encodeURIComponent(rfid)}?month=${encodeURIComponent(month)}`, {
            method: 'GET',
            headers: getAuthHeaders(),
            credentials: 'include',
            cache: 'no-store'
        });
        
        if (response.status === 401) {
            redirectToLogin();
            return;
        }
        
        if (!response.ok) {
            console.error('Failed to load DTR record:', response.status);
            showDTRMessage('Failed to load DTR record.', 'error');
            return;
        }
        
        const result = await response.json();
        if (result.status === 'success' && result.data) {
            const record = result.data.record;
            const dtr = record.dtr || [];
            
            // Update totals
            const totalHoursEl = document.getElementById('dtrTotalHours');
            const totalOtEl = document.getElementById('dtrTotalOt');
            const totalUtEl = document.getElementById('dtrTotalUt');
            if (totalHoursEl) totalHoursEl.textContent = record.total_hours || '0.00';
            if (totalOtEl) totalOtEl.textContent = record.total_ot || '0.00';
            if (totalUtEl) totalUtEl.textContent = record.total_ut || '0.00';
            
            // Update DTR table
            const tbody = document.getElementById('dtrTableBody');
            if (!tbody) return;
            
            if (!dtr || dtr.length === 0) {
                tbody.innerHTML = `<tr><td colspan="10" style="text-align:center;padding:20px;color:var(--text-muted);">
                    No attendance records for this month.
                </td></tr>`;
                showDTRMessage('No attendance records found for this month.', 'info');
                return;
            }
            
            // Display all days
            tbody.innerHTML = dtr.map(day => {
                const isWeekend = day.day === 'Sat' || day.day === 'Sun';
                const isWorkStatus = day.status === 'on_work_status';
                const hasWorkStatus = day.work_status && day.work_status.is_active;
                let rowStyle = '';
                let statusText = day.status || '';
                
                if (isWorkStatus || hasWorkStatus) {
                    rowStyle = 'background-color:#FEF3C7;';
                    if (day.work_status) {
                        const wsLabel = day.work_status.label || day.work_status.type || 'Work Status';
                        const wsPeriod = day.work_status.period || 'whole_day';
                        // Show the period alongside the label so HR/Admin can
                        // immediately see whether this covers the whole day,
                        // only AM, or only PM.
                        statusText = `${wsLabel} (${wsPeriod.replace('_', ' ')})`;
                    } else {
                        statusText = 'WORK STATUS';
                    }
                } else if (isWeekend) {
                    rowStyle = 'background-color:#F3F4F6;color:#9CA3AF;';
                    statusText = 'Weekend';
                }
                
                return `<tr style="${rowStyle}">
                    <td>${escapeHtml(formatDTRDate(day.date))}</td>
                    <td>${escapeHtml(day.day || '')}</td>
                    <td>${escapeHtml(day.am_in || '')}</td>
                    <td>${escapeHtml(day.am_out || '')}</td>
                    <td>${escapeHtml(day.pm_in || '')}</td>
                    <td>${escapeHtml(day.pm_out || '')}</td>
                    <td>${escapeHtml(day.hours || '0.00')}</td>
                    <td>${escapeHtml(day.ut || '0.00')}</td>
                    <td>${escapeHtml(day.ot || '0.00')}</td>
                    <td>${escapeHtml(statusText)}</td>
                </tr>`;
            }).join('');
            
            showDTRMessage(`DTR loaded for ${fullname} (${record.month_display})`, 'success');
        }
    } catch (error) {
        console.error('Error loading DTR record:', error);
        showDTRMessage('Error loading DTR record.', 'error');
    }
}

// Show DTR message
function showDTRMessage(message, type = 'info') {
    const msgEl = document.getElementById('dtrMessage');
    if (!msgEl) return;
    
    const colors = {
        success: '#10B981',
        error: '#EF4444',
        warning: '#F59E0B',
        info: '#3B82F6'
    };
    
    const icons = {
        success: 'fa-check-circle',
        error: 'fa-exclamation-circle',
        warning: 'fa-exclamation-triangle',
        info: 'fa-info-circle'
    };
    
    msgEl.innerHTML = `<i class="fa-solid ${icons[type] || icons.info}"></i> ${message}`;
    msgEl.style.color = colors[type] || colors.info;
    msgEl.style.display = 'block';
    msgEl.className = `dtr-message dtr-message-${type}`;
    
    // Auto hide after 5 seconds for success messages
    if (type === 'success') {
        clearTimeout(msgEl._timeout);
        msgEl._timeout = setTimeout(() => {
            msgEl.style.display = 'none';
        }, 5000);
    }
}

// Build DTR HTML matching the exact two-copy layout from the reference image
function buildDTRHTML(record, dtr, employee) {
    // Name is ALWAYS "LASTNAME, FIRSTNAME" format (lastname first, uppercase).
    // We build this from lastname + firstname directly so we never fall back to
    // a pre-formatted "Firstname Lastname" string coming from the API.
    const rawLast = (employee.lastname || '').trim();
    const rawFirst = (employee.firstname || '').trim();
    let fullname;
    if (rawLast || rawFirst) {
        fullname = rawLast
            ? `${rawLast}, ${rawFirst}`.replace(/,\s*$/, '')
            : rawFirst;
    } else {
        // Last resort: parse an existing "Firstname Lastname" string and flip it
        const src = (employee.fullname || 'Unknown').trim();
        const parts = src.split(/\s+/);
        if (parts.length >= 2) {
            const last = parts.pop();
            fullname = `${last}, ${parts.join(' ')}`;
        } else {
            fullname = src;
        }
    }
    fullname = fullname.toUpperCase();

    const position = employee.position || '';
    const department = employee.department || '';
    const totalUt = record.total_ut || '0.00';

    // Get the month range — formatted "M/D/YY" and derived from whatever
    // month this record actually covers (works for any month, not just now).
    const { from: fromDate, to: toDate } = resolveDTRDateRange(record, dtr);

    // Calculate total working days (A)
    const workingDays = dtr.filter(day => day.status !== 'on_work_status' && day.day !== 'Sat' && day.day !== 'Sun').length;
    const totalWorkingDays = Number(workingDays) || 0;
    const totalUndertime = totalUt;

    // Build the table body rows once — each copy prints the FULL date range (1..end),
    // exactly like the two side-by-side originals on the reference form.
    const tableRows = dtr.map(day => {
        const isWeekend = day.day === 'Sat' || day.day === 'Sun';
        const isWorkStatus = day.status === 'on_work_status' || (day.work_status && day.work_status.is_active);
        const rowStyle = isWeekend ? 'background-color:#f2f2f2;' : (isWorkStatus ? 'background-color:#fef3c7;' : '');
        const ut = day.ut && day.ut !== '0.00' && day.ut !== 0 ? day.ut : '';
        const ot = day.ot && day.ot !== '0.00' && day.ot !== 0 ? day.ot : '';

        return `
            <tr style="${rowStyle}">
                <td class="c-date">${formatDTRDate(day.date)}</td>
                <td class="c-day">${day.day || ''}</td>
                <td class="c-time">${day.am_in || ''}</td>
                <td class="c-time">${day.am_out || ''}</td>
                <td class="c-time">${day.pm_in || ''}</td>
                <td class="c-time">${day.pm_out || ''}</td>
                <td class="c-small">${ut}</td>
                <td class="c-small">${ot}</td>
            </tr>`;
    }).join('');

    // One full copy (either Employee's or Personnel's) — identical table/content,
    // only the bottom copy label (and Personnel-only "Recorded By" block) differ.
    function buildCopy(copyLabel, isPersonnelCopy) {
        return `
        <div class="dtr-copy">
            <div class="dtr-title">DAILY TIME RECORD</div>
            <div class="dtr-title-space">&nbsp;</div>
            <div class="dtr-subtitle">DAILY TIME RECORD</div>
            <div class="dtr-daterange">From: ${fromDate} To: ${toDate}</div>
            <div class="dtr-title-space">&nbsp;</div>

            <div class="dtr-info">
                <div class="info-row"><span class="info-label">Name :</span><span class="info-value name">${fullname}</span></div>
                <div class="info-row"><span class="info-label">Position :</span><span class="info-value">${position}</span></div>
                <div class="info-row"><span class="info-label">Department :</span><span class="info-value">${department}</span></div>
                <div class="info-row two-col">
                    <span class="info-half"><span class="info-label">Regular Time :</span><span class="info-value">${employee.regularTime || 'DEFAULT'}</span></span>
                    <span class="info-half payroll-half"><span class="info-label label-auto">Payroll No. :</span><span class="info-value payroll-underline">1</span></span>
                </div>
            </div>

            <table class="dtr-table">
                <colgroup>
                    <col class="col-date"><col class="col-day">
                    <col class="col-time"><col class="col-time">
                    <col class="col-time"><col class="col-time">
                    <col class="col-small"><col class="col-small">
                </colgroup>
                <thead>
                    <tr class="grp-row">
                        <th colspan="2">WORKING</th>
                        <th colspan="2">A M</th>
                        <th colspan="2">P M</th>
                        <th colspan="2">HOURS</th>
                    </tr>
                    <tr class="sub-row">
                        <th>Date</th>
                        <th>Days</th>
                        <th>In 1</th>
                        <th>Out 1</th>
                        <th>In 2</th>
                        <th>Out 2</th>
                        <th>UT</th>
                        <th>OT</th>
                    </tr>
                </thead>
                <tbody>
                    ${tableRows}
                </tbody>
            </table>

            <div class="dtr-summary">
                <div class="summary-line">
                    <span class="summary-item"><label>A =</label><span class="fill">${totalWorkingDays.toFixed(2)}</span></span>
                    <span class="summary-item"><label>ROT =</label><span class="fill">0.00</span></span>
                    <span class="summary-item"><label>LOT =</label><span class="fill">&nbsp;</span></span>
                </div>
                <div class="summary-line">
                    <span class="summary-item"><label>U =</label><span class="fill">${totalUndertime}</span></span>
                    <span class="summary-item"><label>SOT =</label><span class="fill">&nbsp;</span></span>
                </div>
            </div>

            <div class="dtr-cert">
                I Certify on my honor that the above is a true and correct report of the hours work perfomed, record of which was daily at the time of arrival and departure from office.
            </div>

            <div class="dtr-sig">
                <div class="sig-line"></div>
                <div class="sig-caption">Signature</div>
            </div>

            <div class="dtr-divider">${'&#61;'.repeat(160)}</div>

            <div class="dtr-verified-label">VERIFIED as to the prescribed office hours</div>

            <div class="dtr-sig">
                <div class="sig-line"></div>
                <div class="sig-caption">In Charge</div>
            </div>

            <div class="dtr-copy-tag">&gt;&gt;&gt;&gt;&gt;${copyLabel}</div>

            ${isPersonnelCopy ? `
            <div class="dtr-recorded">
                <div class="recorded-row"><span class="recorded-label">RECORDED BY</span><span class="recorded-colon">:</span><span class="recorded-line"></span></div>
                <div class="recorded-row"><span class="recorded-label">DATE</span><span class="recorded-colon">:</span><span class="recorded-line"></span></div>
            </div>` : ''}
        </div>`;
    }

    const employeeCopyHTML = buildCopy("EMPLOYEE'S COPY", false);
    const personnelCopyHTML = buildCopy("PERSONNEL'S COPY", true);

    // Build the complete DTR HTML — two full copies side by side on one page,
    // matching the exact structure of the reference form image.
    return `
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="UTF-8">
            <title>Daily Time Record - ${fullname}</title>
            <style>
                @page {
                    size: A4 portrait;
                    margin: 8mm 8mm;
                }
                * {
                    margin: 0;
                    padding: 0;
                    box-sizing: border-box;
                }
                html, body {
                    width: 100%;
                    height: 100%;
                }
                body {
                    font-family: Arial, Helvetica, sans-serif;
                    font-size: 9px;
                    color: #000;
                    font-weight: bold;
                }
                .dtr-page {
                    display: flex;
                    align-items: stretch;
                    width: 100%;
                }
                .dtr-copy {
                    flex: 1 1 50%;
                    width: 50%;
                    min-width: 0;
                    padding: 2px 14px;
                }
                .dtr-vertical-divider {
                    width: 0;
                    border-left: 1px solid #000;
                    margin: 4px 4px 4px 0;
                }
                .dtr-title {
                    text-align: center;
                    font-size: 15px;
                    font-weight: bold;
                    text-transform: uppercase;
                }
                .dtr-title-space {
                    height: 8px;
                    line-height: 8px;
                }
                .dtr-subtitle {
                    text-align: center;
                    font-size: 9px;
                    font-weight: bold;
                    text-transform: uppercase;
                }
                .dtr-daterange {
                    text-align: center;
                    font-size: 9px;
                    font-weight: bold;
                }
                .dtr-info {
                    font-size: 9px;
                    margin-bottom: 5px;
                    font-weight: bold;
                }
                .info-row {
                    display: flex;
                    gap: 0;
                    padding: 1px 0;
                }
                .info-row.two-col {
                    justify-content: space-between;
                }
                .info-half {
                    display: flex;
                    gap: 0;
                    align-items: flex-end;
                }
                /* Payroll No. group: the "1" sits DIRECTLY against the
                   "Payroll No. :" colon — no gap at all. */
                .payroll-half {
                    gap: 0;
                }
                .info-label {
                    font-weight: bold;
                    white-space: nowrap;
                    display: inline-block;
                    width: 90px;
                    flex-shrink: 0;
                }
                .info-label.label-auto {
                    width: auto;
                }
                .info-value {
                    font-weight: bold;
                    border-bottom: 1px solid transparent;
                    margin-left: 0;
                }
                .info-value.name {
                    font-weight: bold;
                    text-transform: uppercase;
                }
                /* Payroll No. value with a WIDER underline under the "1",
                   pulled flush against the label colon. */
                .info-value.payroll-underline {
                    border-bottom: 1px solid #000;
                    min-width: 42px;
                    text-align: center;
                    display: inline-block;
                    font-weight: bold;
                    margin-left: 0;
                }
                .dtr-table {
                    width: 100%;
                    border-collapse: collapse;
                    table-layout: fixed;
                    font-size: 12px;
                    margin-bottom: 4px;
                }
                .dtr-table col.col-date { width: 15%; }
                .dtr-table col.col-day { width: 11%; }
                .dtr-table col.col-time { width: 13%; }
                .dtr-table col.col-small { width: 9%; }
                .dtr-table th,
                .dtr-table td {
                    border: 1px solid #000;
                    text-align: center;
                    padding: 1px 2px;
                    overflow: hidden;
                    white-space: nowrap;
                    font-weight: bold;
                }
                .dtr-table thead tr.grp-row th {
                    padding: 6px 2px;
                    font-size: 13px;
                    letter-spacing: 0.5px;
                    font-weight: bold;
                }
                .dtr-table thead tr.sub-row th {
                    padding: 6px 2px;
                    font-size: 12px;
                    background-color: #fff;
                    font-weight: bold;
                }
                .dtr-table thead th {
                    font-weight: bold;
                    background-color: #fff;
                }
                .dtr-table tbody td {
                    font-size: 12px;
                    height: 18px;
                    line-height: 1;
                    padding: 2px 2px;
                    vertical-align: middle;
                    font-weight: bold;
                }
                /* Time values (In 1 / Out 1 / In 2 / Out 2) stay normal weight */
                .dtr-table tbody td.c-time {
                    font-weight: normal;
                }
                /* UT and OT numbers stay normal weight too */
                .dtr-table tbody td.c-small {
                    font-weight: normal;
                }

                /* ===== Bottom-of-form block — everything bold ===== */

                /* A = / ROT = / LOT =  and  U = / SOT = summary lines */
                .dtr-summary {
                    font-size: 9px;
                    margin: 3px 0;
                    font-weight: bold;
                }
                .summary-line {
                    display: grid;
                    grid-template-columns: 1fr 1fr 1fr;
                    column-gap: 10px;
                    padding: 1px 0;
                }
                .summary-item {
                    display: flex;
                    align-items: flex-end;
                    gap: 3px;
                }
                .summary-item label {
                    font-weight: bold;
                    white-space: nowrap;
                }
                .summary-item .fill {
                    border-bottom: 1px solid #000;
                    min-width: 34px;
                    display: inline-block;
                    text-align: center;
                    font-weight: bold;
                }

                /* "I Certify on my honor..." paragraph */
                .dtr-cert {
                    font-size: 7.5px;
                    text-align: center;
                    line-height: 1.35;
                    margin: 6px 0 2px 0;
                    padding: 0 45px;
                    font-weight: bold;
                }

                /* Signature / In Charge line + caption */
                .dtr-sig {
                    text-align: center;
                    margin-top: 16px;
                }
                .dtr-sig .sig-line {
                    border-top: 1px solid #000;
                    width: 85%;
                    margin: 0 auto;
                }
                .dtr-sig .sig-caption {
                    font-size: 8px;
                    font-weight: bold;
                    margin-top: 1px;
                }

                /* ===== divider line ===== */
                .dtr-divider {
                    font-size: 7px;
                    line-height: 1;
                    letter-spacing: -0.5px;
                    margin: 6px 0 2px 0;
                    overflow: hidden;
                    white-space: nowrap;
                    font-weight: bold;
                }

                /* VERIFIED as to the prescribed office hours */
                .dtr-verified-label {
                    text-align: center;
                    font-size: 8px;
                    font-weight: bold;
                    margin-bottom: 2px;
                }

                /* >>>>>EMPLOYEE'S COPY / >>>>>PERSONNEL'S COPY */
                .dtr-copy-tag {
                    font-weight: bold;
                    font-size: 8.5px;
                    margin-top: 6px;
                }

                /* Personnel-only RECORDED BY : / DATE : block */
                .dtr-recorded {
                    margin-top: 10px;
                    font-size: 8.5px;
                    font-weight: bold;
                    display: flex;
                    flex-direction: column;
                    align-items: center;
                    padding: 0 45px;
                    gap: 4px;
                }
                .recorded-row {
                    display: flex;
                    align-items: flex-end;
                    justify-content: flex-start;
                    width: 100%;
                }
                .recorded-row .recorded-label {
                    font-weight: bold;
                    white-space: nowrap;
                    flex-shrink: 0;
                    display: inline-block;
                    width: 75px;
                }
                .recorded-row .recorded-colon {
                    font-weight: bold;
                    flex-shrink: 0;
                    margin-right: 6px;
                }
                .recorded-row .recorded-line {
                    flex: 1;
                    border-bottom: 1px solid #000;
                    height: 10px;
                    min-width: 120px;
                }

                @media print {
                    .dtr-page {
                        page-break-inside: avoid;
                    }
                }
            </style>
        </head>
        <body>
            <div class="dtr-page">
                ${employeeCopyHTML}
                <div class="dtr-vertical-divider"></div>
                ${personnelCopyHTML}
            </div>
        </body>
        </html>
    `;
}

// Generate DTR PDF - Builds HTML and opens print dialog for PDF
async function generateDTRPDF() {
    const select = document.getElementById('dtrEmployeeSelect');
    const monthSelect = document.getElementById('dtrMonthSelect');
    const rfid = select.value;
    const month = monthSelect.value;
    
    if (!rfid || rfid === '') {
        showDTRMessage('Please select an employee first.', 'warning');
        return;
    }
    
    if (!month || month === '') {
        showDTRMessage('Please select a month.', 'warning');
        return;
    }
    
    try {
        showDTRMessage('Generating PDF...', 'info');

        // First, get the DTR data to build the HTML
        const response = await fetch(`${dashboardApiBaseUrl}/api/dtr/record/${encodeURIComponent(rfid)}?month=${encodeURIComponent(month)}`, {
            method: 'GET',
            headers: getAuthHeaders(),
            credentials: 'include',
            cache: 'no-store'
        });
        
        if (response.status === 401) {
            redirectToLogin();
            return;
        }
        
        if (!response.ok) {
            showDTRMessage('Failed to load DTR data.', 'error');
            return;
        }
        
        const result = await response.json();
        if (result.status !== 'success' || !result.data) {
            showDTRMessage('No DTR data available.', 'error');
            return;
        }
        
        const record = result.data.record;
        const dtr = record.dtr || [];
        const employee = resolveDTREmployeeInfo(select, record.employee, record);
        
        // Build the DTR HTML with the exact format from the image
        const dtrHTML = buildDTRHTML(record, dtr, employee);
        
        // Generate PDF from HTML using print
        const printWindow = window.open('', '_blank', 'width=1100,height=800');
        if (!printWindow) {
            showDTRMessage('Please allow popups for this site to generate PDF.', 'warning');
            return;
        }
        
        printWindow.document.write(dtrHTML);
        printWindow.document.close();
        
        printWindow.onload = function() {
            setTimeout(() => {
                printWindow.print();
                // Don't close the window immediately so user can save as PDF
                // The user can close it manually after printing
            }, 500);
        };
        
        showDTRMessage('PDF generated successfully!', 'success');
    } catch (error) {
        console.error('Error generating PDF:', error);
        showDTRMessage('Error generating PDF.', 'error');
    }
}

// Print DTR - Uses browser print functionality with formatted HTML
function printDTR() {
    const select = document.getElementById('dtrEmployeeSelect');
    const monthSelect = document.getElementById('dtrMonthSelect');
    const rfid = select.value;
    const month = monthSelect.value;
    
    if (!rfid || rfid === '') {
        showDTRMessage('Please select an employee first.', 'warning');
        return;
    }
    
    if (!month || month === '') {
        showDTRMessage('Please select a month.', 'warning');
        return;
    }
    
    // Fetch the DTR data and print
    fetch(`${dashboardApiBaseUrl}/api/dtr/record/${encodeURIComponent(rfid)}?month=${encodeURIComponent(month)}`, {
        method: 'GET',
        headers: getAuthHeaders(),
        credentials: 'include',
        cache: 'no-store'
    })
    .then(response => {
        if (response.status === 401) {
            redirectToLogin();
            return;
        }
        if (!response.ok) {
            showDTRMessage('Failed to load DTR data.', 'error');
            return;
        }
        return response.json();
    })
    .then(result => {
        if (!result || result.status !== 'success' || !result.data) {
            showDTRMessage('No DTR data available.', 'error');
            return;
        }
        
        const record = result.data.record;
        const dtr = record.dtr || [];
        const employee = resolveDTREmployeeInfo(select, record.employee, record);
        
        // Build the DTR HTML
        const dtrHTML = buildDTRHTML(record, dtr, employee);
        
        // Print the DTR
        const printWindow = window.open('', '_blank', 'width=1100,height=800');
        if (!printWindow) {
            showDTRMessage('Please allow popups for this site to print.', 'warning');
            return;
        }
        
        printWindow.document.write(dtrHTML);
        printWindow.document.close();
        
        printWindow.onload = function() {
            setTimeout(() => {
                printWindow.print();
            }, 500);
        };
    })
    .catch(error => {
        console.error('Error printing DTR:', error);
        showDTRMessage('Error printing DTR.', 'error');
    });
}

// ============ END DTR FUNCTIONS ============

// ============ REPORT FUNCTIONS ============

// Report type labels for display
const REPORT_LABELS = {
    'daily': 'Daily Attendance',
    'weekly': 'Weekly Attendance',
    'monthly': 'Monthly Attendance',
    'yearly': 'Yearly Attendance',
    'summary': 'Attendance Summary',
    'absent': 'Absent Employees',
    'leave': 'Work Status Report',
    // Alias so the "Work Status Report" card (data-report="work-status")
    // also resolves to a proper label instead of "undefined".
    'work-status': 'Work Status Report',
    'rfid-logs': 'RFID Scan Logs'
};

// Print a report - opens a new window with the report data and triggers print.
// Uses the /api/reports/<report_type>/print endpoint, which now returns real
// data pulled straight from the attendance database (not sample data).
async function printReport(reportType) {
    try {
        showNotification(`Preparing ${REPORT_LABELS[reportType] || reportType} report for printing...`, 'info');
        
        const response = await fetch(`${dashboardApiBaseUrl}/api/reports/${reportType}/print`, {
            method: 'GET',
            headers: getAuthHeaders(),
            credentials: 'include',
            cache: 'no-store'
        });
        
        if (response.status === 401) {
            redirectToLogin();
            return;
        }
        
        if (!response.ok) {
            const err = await response.json().catch(() => ({}));
            showNotification(err.message || 'Failed to load report data.', 'error');
            return;
        }
        
        const result = await response.json();
        if (result.status !== 'success' || !result.data) {
            showNotification('No report data available.', 'error');
            return;
        }
        
        // Build printable HTML from report data
        const printHTML = buildReportPrintHTML(reportType, result.data);
        
        const printWindow = window.open('', '_blank', 'width=1100,height=800');
        if (!printWindow) {
            showNotification('Please allow popups for this site to print.', 'warning');
            return;
        }
        
        printWindow.document.write(printHTML);
        printWindow.document.close();
        
        printWindow.onload = function() {
            setTimeout(() => {
                printWindow.print();
            }, 500);
        };
        
        showNotification('Report ready for printing!', 'success');
    } catch (error) {
        console.error('Error printing report:', error);
        showNotification('Error printing report.', 'error');
    }
}

// Download a report as PDF - hits the backend PDF endpoint which builds the
// PDF from real attendance data and streams it back as a blob.
async function downloadReportPDF(reportType) {
    try {
        showNotification(`Generating ${REPORT_LABELS[reportType] || reportType} PDF...`, 'info');
        
        const response = await fetch(`${dashboardApiBaseUrl}/api/reports/${reportType}/pdf`, {
            method: 'GET',
            headers: getAuthHeaders(),
            credentials: 'include',
            cache: 'no-store'
        });
        
        if (response.status === 401) {
            redirectToLogin();
            return;
        }
        
        if (!response.ok) {
            const err = await response.json().catch(() => ({}));
            showNotification(err.message || 'Failed to generate PDF.', 'error');
            return;
        }
        
        // Get the blob and trigger download
        const blob = await response.blob();
        const url = window.URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = `${reportType}-report-${new Date().toISOString().slice(0, 10)}.pdf`;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        window.URL.revokeObjectURL(url);
        
        showNotification('PDF downloaded successfully!', 'success');
    } catch (error) {
        console.error('Error downloading PDF:', error);
        showNotification('Error generating PDF.', 'error');
    }
}

// Download a report as Excel - hits the backend Excel endpoint which builds
// the spreadsheet from real attendance data and streams it back as a blob.
async function downloadReportExcel(reportType) {
    try {
        showNotification(`Generating ${REPORT_LABELS[reportType] || reportType} Excel...`, 'info');
        
        const response = await fetch(`${dashboardApiBaseUrl}/api/reports/${reportType}/excel`, {
            method: 'GET',
            headers: getAuthHeaders(),
            credentials: 'include',
            cache: 'no-store'
        });
        
        if (response.status === 401) {
            redirectToLogin();
            return;
        }
        
        if (!response.ok) {
            const err = await response.json().catch(() => ({}));
            showNotification(err.message || 'Failed to generate Excel.', 'error');
            return;
        }
        
        // Get the blob and trigger download
        const blob = await response.blob();
        const url = window.URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = `${reportType}-report-${new Date().toISOString().slice(0, 10)}.xlsx`;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        window.URL.revokeObjectURL(url);
        
        showNotification('Excel downloaded successfully!', 'success');
    } catch (error) {
        console.error('Error downloading Excel:', error);
        showNotification('Error generating Excel.', 'error');
    }
}

// Generate a report (trigger background generation)
async function generateReport(reportType) {
    try {
        showNotification(`Generating ${REPORT_LABELS[reportType] || reportType} report...`, 'info');
        
        const response = await fetch(`${dashboardApiBaseUrl}/api/reports/${reportType}/generate`, {
            method: 'POST',
            headers: getAuthHeaders(),
            credentials: 'include'
        });
        
        if (response.status === 401) {
            redirectToLogin();
            return;
        }
        
        const result = await response.json().catch(() => ({}));
        
        if (!response.ok) {
            showNotification(result.message || 'Failed to generate report.', 'error');
            return;
        }
        
        showNotification(`${REPORT_LABELS[reportType] || reportType} report generated successfully!`, 'success');
    } catch (error) {
        console.error('Error generating report:', error);
        showNotification('Error generating report.', 'error');
    }
}

// Generate all reports in one click
async function generateAllReports() {
    const reportTypes = ['daily', 'weekly', 'monthly', 'yearly', 'summary', 'absent', 'leave', 'rfid-logs'];
    
    showNotification('Generating all reports...', 'info');
    
    let successCount = 0;
    let failCount = 0;
    
    for (const reportType of reportTypes) {
        try {
            const response = await fetch(`${dashboardApiBaseUrl}/api/reports/${reportType}/generate`, {
                method: 'POST',
                headers: getAuthHeaders(),
                credentials: 'include'
            });
            
            if (response.ok) {
                successCount++;
            } else {
                failCount++;
            }
        } catch (error) {
            failCount++;
        }
    }
    
    if (failCount === 0) {
        showNotification(`All ${successCount} reports generated successfully!`, 'success');
    } else {
        showNotification(`${successCount} reports generated, ${failCount} failed.`, 'warning');
    }
}

// Build printable HTML from report data returned by the backend.
// The backend now returns real rows pulled from the attendance database,
// so the printout shows actual employee data instead of placeholder text.
function buildReportPrintHTML(reportType, data) {
    const title = data.title || `${REPORT_LABELS[reportType] || reportType} Report`;
    const generatedAt = new Date().toLocaleString();
    
    // Build rows from data if available
    let tableHTML = '';
    
    if (data.rows && data.rows.length > 0) {
        // Get headers from the first row
        const headers = Object.keys(data.rows[0]);
        
        tableHTML = `
            <table>
                <thead>
                    <tr>
                        ${headers.map(h => `<th>${escapeHtml(h)}</th>`).join('')}
                    </tr>
                </thead>
                <tbody>
                    ${data.rows.map(row => `
                        <tr>
                            ${headers.map(h => `<td>${escapeHtml(row[h] ?? '')}</td>`).join('')}
                        </tr>
                    `).join('')}
                </tbody>
            </table>
        `;
    } else if (data.content) {
        tableHTML = `<div class="content">${data.content}</div>`;
    } else {
        tableHTML = `<div class="content">No data available for this report.</div>`;
    }
    
    // Build summary section if available
    let summaryHTML = '';
    if (data.summary) {
        summaryHTML = `
            <div class="summary">
                <h3>Summary</h3>
                <table class="summary-table">
                    ${Object.entries(data.summary).map(([key, value]) => `
                        <tr>
                            <td class="summary-label">${escapeHtml(key)}</td>
                            <td class="summary-value">${escapeHtml(value)}</td>
                        </tr>
                    `).join('')}
                </table>
            </div>
        `;
    }
    
    return `
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="UTF-8">
            <title>${escapeHtml(title)}</title>
            <style>
                @page {
                    size: A4 portrait;
                    margin: 15mm;
                }
                * {
                    margin: 0;
                    padding: 0;
                    box-sizing: border-box;
                }
                body {
                    font-family: Arial, Helvetica, sans-serif;
                    font-size: 11px;
                    color: #000;
                    padding: 20px;
                }
                .header {
                    text-align: center;
                    margin-bottom: 20px;
                    padding-bottom: 15px;
                    border-bottom: 2px solid #000;
                }
                .header h1 {
                    font-size: 20px;
                    font-weight: bold;
                    margin-bottom: 4px;
                }
                .header .subtitle {
                    font-size: 12px;
                    color: #444;
                }
                .header .timestamp {
                    font-size: 10px;
                    color: #666;
                    margin-top: 8px;
                }
                .institution {
                    font-size: 11px;
                    font-weight: bold;
                    margin-bottom: 2px;
                }
                table {
                    width: 100%;
                    border-collapse: collapse;
                    margin-bottom: 20px;
                }
                th, td {
                    border: 1px solid #333;
                    padding: 6px 8px;
                    text-align: left;
                    font-size: 10px;
                }
                th {
                    background-color: #e5e7eb;
                    font-weight: bold;
                    text-align: center;
                }
                td {
                    text-align: center;
                }
                .summary {
                    margin-top: 20px;
                    padding: 15px;
                    background: #f9fafb;
                    border: 1px solid #e5e7eb;
                    border-radius: 4px;
                }
                .summary h3 {
                    font-size: 13px;
                    margin-bottom: 10px;
                    padding-bottom: 5px;
                    border-bottom: 1px solid #e5e7eb;
                }
                .summary-table {
                    width: auto;
                    margin-bottom: 0;
                }
                .summary-table td {
                    border: none;
                    padding: 4px 12px 4px 0;
                    text-align: left;
                    font-size: 11px;
                }
                .summary-label {
                    font-weight: bold;
                    color: #444;
                }
                .summary-value {
                    color: #000;
                }
                .content {
                    font-size: 12px;
                    line-height: 1.6;
                    margin-bottom: 20px;
                }
                .footer {
                    margin-top: 30px;
                    padding-top: 15px;
                    border-top: 1px solid #ccc;
                    font-size: 9px;
                    color: #666;
                    text-align: center;
                }
                @media print {
                    body {
                        padding: 0;
                    }
                    .header {
                        page-break-after: avoid;
                    }
                    table {
                        page-break-inside: auto;
                    }
                    tr {
                        page-break-inside: avoid;
                    }
                }
            </style>
        </head>
        <body>
            <div class="header">
                <div class="institution">ISPSC Tagudin Campus</div>
                <h1>${escapeHtml(title)}</h1>
                <div class="subtitle">TAPIN RFID-Based Attendance Management System</div>
                <div class="timestamp">Generated: ${escapeHtml(generatedAt)}</div>
            </div>
            ${tableHTML}
            ${summaryHTML}
            <div class="footer">
                <p>This is a system-generated report. TAPIN RFID Attendance System &copy; ${new Date().getFullYear()} ISPSC Tagudin Campus</p>
            </div>
        </body>
        </html>
    `;
}

// ============ END REPORT FUNCTIONS ============

// Update Realtime Attendance rate circle and progress bars
function updateAttendanceRate(stats) {
    const total = stats.total_employees || 0;
    const present = stats.present_today || 0;
    const absent = stats.absent_today || 0;
    const onWorkStatus = stats.on_work_status || 0;
    const rate = stats.attendance_rate || 0;

    // Update rate circle
    const rateCircle = document.querySelector('.rate-circle');
    const rateValue = document.querySelector('.rate-value');
    if (rateCircle && rateValue) {
        const percentage = Math.min(rate, 100);
        rateCircle.style.background = `conic-gradient(var(--success) 0% ${percentage}%, var(--border) ${percentage}% 100%)`;
        rateValue.textContent = `${percentage}%`;
    }

    // Update attendance rate text
    const rateText = document.getElementById('attendanceRateText');
    if (rateText) {
        rateText.textContent = `${present} of ${total} employees present`;
    }

    // Progress bar + count elements actually present in the HTML:
    //   Present     -> #presentFill    / #presentCount
    //   Absent      -> #absentFill     / #absentCount
    //   Work Status -> #workStatusFill / #workStatusCount
    //
    // (The old code referenced #leaveFill / #leaveCount and #lateFill /
    //  #lateCount — those IDs no longer exist in the HTML, so the
    //  "On Work Status" bar and count were stuck at 0%.)
    const presentFill = document.getElementById('presentFill');
    const absentFill = document.getElementById('absentFill');
    const workStatusFill = document.getElementById('workStatusFill');

    const presentCount = document.getElementById('presentCount');
    const absentCount = document.getElementById('absentCount');
    const workStatusCount = document.getElementById('workStatusCount');

    if (total > 0) {
        const presentPct = (present / total) * 100;
        const absentPct = (absent / total) * 100;
        const workStatusPct = (onWorkStatus / total) * 100;

        if (presentFill) presentFill.style.width = `${Math.min(presentPct, 100)}%`;
        if (absentFill) absentFill.style.width = `${Math.min(absentPct, 100)}%`;
        if (workStatusFill) workStatusFill.style.width = `${Math.min(workStatusPct, 100)}%`;

        if (presentCount) presentCount.textContent = present;
        if (absentCount) absentCount.textContent = absent;
        if (workStatusCount) workStatusCount.textContent = onWorkStatus;
    } else {
        if (presentFill) presentFill.style.width = '0%';
        if (absentFill) absentFill.style.width = '0%';
        if (workStatusFill) workStatusFill.style.width = '0%';

        if (presentCount) presentCount.textContent = 0;
        if (absentCount) absentCount.textContent = 0;
        if (workStatusCount) workStatusCount.textContent = 0;
    }
}

async function loadDashboardData() {
    try {
        const response = await fetch(`${dashboardApiBaseUrl}/api/dashboard-data`, {
            method: 'GET',
            headers: getAuthHeaders(),
            credentials: 'include',
            cache: 'no-store'
        });
        if (response.status === 401) {
            redirectToLogin();
            return;
        }
        if (!response.ok) throw new Error('Dashboard data unavailable');

        const result = await response.json();
        const data = result.data || {};
        const stats = data.stats || {};
        
        updateDashboardStatistics(stats);
        updateAttendanceRate(stats);
        updateEmployeeTable(data.users || []);
        updateScanTable(data.scans || []);
        
        // Update attendance table with pagination
        updateAttendanceTable(data.scans || []);
        
        // Populate department filter
        populateAttendanceDepartmentFilter(data.scans || []);
        
        updateDeviceDisplay(data.devices || []);
        
        // Update system status indicators
        updateSystemStatus(data);
        
        // Update activity timeline from dashboard data or fetch separately
        if (data.activities && data.activities.length > 0) {
            updateActivityTimeline(data.activities);
        } else {
            // If activities not in dashboard data, fetch separately
            loadActivityFeed();
        }

        // Remember the latest stats so the daily-stats card refresher can
        // fill in the gaps (total employees, absent, etc.) when it updates
        // ONLY the present + scan counts.
        window.__lastStats = stats;

        const latestScanTime = document.getElementById('latestScanTime');
        if (latestScanTime) {
            const latest = data.latest_scan;
            latestScanTime.textContent = latest
                ? `${latest.scanned_at}${latest.employee ? ` - ${latest.employee.firstname} ${latest.employee.lastname}` : ''}`
                : 'No scan received';
        }
    } catch (error) {
        updateDeviceDisplay([]);
    }
}

// Update system status indicators (RFID Reader, Database, Server, Network)
function updateSystemStatus(data) {
    const devices = data.devices || [];
    const isOnline = devices.length > 0;
    
    // RFID Reader status
    const readerIndicator = document.getElementById('readerStatusIndicator');
    const readerStatusText = document.getElementById('readerStatusText');
    if (readerIndicator) {
        readerIndicator.className = `status-indicator ${isOnline ? 'status-online' : 'status-offline'}`;
    }
    if (readerStatusText) {
        readerStatusText.className = `status-text ${isOnline ? 'text-online' : 'text-offline'}`;
        readerStatusText.innerHTML = isOnline
            ? '<i class="fa-solid fa-circle-check"></i> Online'
            : '<i class="fa-solid fa-circle-xmark"></i> Offline';
    }
    
    // Database status - always online if API responds
    // Server status - always online if API responds
    // Network status - always online if API responds
}

// ============ PROFILE MODAL FUNCTIONS ============

// Open the Profile modal and populate it from the logged-in admin's own data
function openProfileModal() {
    const modal = document.getElementById('profileModal');
    if (!modal) return;

    const user = currentAdminUser || {};
    const fullname = user.fullname
        || `${user.firstname || ''} ${user.lastname || ''}`.trim()
        || user.username
        || 'Unknown';
    const role = (user.role || 'employee').toUpperCase();

    const titleEl = document.getElementById('profileModalTitle');
    const subtitleEl = document.getElementById('profileModalSubtitle');
    const avatarEl = document.getElementById('profileModalAvatar');
    const emailEl = document.getElementById('profileEmail');
    const phoneEl = document.getElementById('profilePhone');
    const departmentEl = document.getElementById('profileDepartment');
    const employeeIdEl = document.getElementById('profileEmployeeId');
    const roleEl = document.getElementById('profileRole');

    if (titleEl) titleEl.textContent = fullname;
    if (subtitleEl) subtitleEl.textContent = role;
    if (avatarEl) avatarEl.textContent = initials(user) !== '--' ? initials(user) : initialsFromFullname(fullname);
    if (emailEl) emailEl.textContent = user.email || '--';
    if (phoneEl) phoneEl.textContent = user.phone || '--';
    if (departmentEl) departmentEl.textContent = user.department || '--';
    if (employeeIdEl) employeeIdEl.textContent = user.employeeid || user.uid || '--';
    const genderEl = document.getElementById('profileGender');
    if (genderEl) genderEl.textContent = user.gender || '--';
    if (roleEl) roleEl.textContent = role;

    modal.classList.remove('hidden');
}

// Close the Profile modal
function closeProfileModal() {
    const modal = document.getElementById('profileModal');
    if (modal) modal.classList.add('hidden');
}

// ============ SETTINGS FUNCTIONS ============

// Open the Settings modal and load the current settings into it
function openSettingsModal() {
    const modal = document.getElementById('settingsModal');
    if (!modal) return;
    modal.classList.remove('hidden');
    loadSettings();
}

// Close the Settings modal
function closeSettingsModal() {
    const modal = document.getElementById('settingsModal');
    if (modal) modal.classList.add('hidden');
}

// Load settings
async function loadSettings() {
    try {
        const response = await fetch(`${dashboardApiBaseUrl}/api/settings`, {
            method: 'GET',
            headers: getAuthHeaders(),
            credentials: 'include',
            cache: 'no-store'
        });
        
        if (response.status === 401) {
            redirectToLogin();
            return;
        }
        
        if (!response.ok) {
            console.error('Failed to load settings:', response.status);
            return;
        }
        
        const result = await response.json();
        if (result.status === 'success' && result.data) {
            populateSettingsForm(result.data);
            
            // Update version display with GitHub version info
            updateVersionDisplay(result.data);
        }
    } catch (error) {
        console.error('Error loading settings:', error);
    }
}

// Populate settings form with data
function populateSettingsForm(settings) {
    // Attendance settings
    if (settings.attendance) {
        const att = settings.attendance;
        document.getElementById('settingsWorkStart').value = att.work_start || '08:00';
        document.getElementById('settingsWorkEnd').value = att.work_end || '17:00';
        document.getElementById('settingsLunchStart').value = att.lunch_start || '12:00';
        document.getElementById('settingsLunchEnd').value = att.lunch_end || '13:00';
        document.getElementById('settingsGracePeriod').value = att.grace_period || 10;
    }
    
    // Institution settings
    if (settings.institution) {
        const inst = settings.institution;
        document.getElementById('settingsInstitutionName').value = inst.name || '';
        document.getElementById('settingsSystemName').value = inst.system_name || '';
        document.getElementById('settingsAcademicYear').value = inst.academic_year || '';
        document.getElementById('settingsHREmail').value = inst.hr_email || '';
    }
}

// Update version display with GitHub version info
function updateVersionDisplay(settings) {
    const versionInput = document.getElementById('settingsVersion');
    const versionStatus = document.getElementById('settingsVersionStatus');
    
    if (!versionInput) return;
    
    const currentVersion = settings.system?.version || '1.0.0';
    const githubVersion = settings.system?.github_version || null;
    
    // If GitHub version exists, use that as the displayed version
    // Otherwise use the current version from settings
    const displayVersion = githubVersion || currentVersion;
    versionInput.value = displayVersion;
    
    // Hide the status message completely
    if (versionStatus) {
        versionStatus.style.display = 'none';
    }
}

// Save settings
async function saveSettings() {
    const settingsData = {
        attendance: {
            work_start: document.getElementById('settingsWorkStart').value,
            work_end: document.getElementById('settingsWorkEnd').value,
            lunch_start: document.getElementById('settingsLunchStart').value,
            lunch_end: document.getElementById('settingsLunchEnd').value,
            grace_period: parseInt(document.getElementById('settingsGracePeriod').value) || 10
        },
        institution: {
            name: document.getElementById('settingsInstitutionName').value,
            system_name: document.getElementById('settingsSystemName').value,
            academic_year: document.getElementById('settingsAcademicYear').value,
            hr_email: document.getElementById('settingsHREmail').value
        }
    };
    
    const msgEl = document.getElementById('settingsMessage');
    msgEl.style.display = 'block';
    msgEl.style.color = '#3B82F6';
    msgEl.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i> Saving settings...';
    
    try {
        const response = await fetch(`${dashboardApiBaseUrl}/api/settings`, {
            method: 'PUT',
            headers: {
                ...getAuthHeaders(),
                'Content-Type': 'application/json'
            },
            body: JSON.stringify(settingsData),
            credentials: 'include'
        });
        
        if (response.status === 401) {
            redirectToLogin();
            return;
        }
        
        const result = await response.json();
        
        if (!response.ok) {
            msgEl.style.color = '#EF4444';
            msgEl.innerHTML = `<i class="fa-solid fa-exclamation-circle"></i> ${result.message || 'Failed to save settings.'}`;
            return;
        }
        
        msgEl.style.color = '#10B981';
        msgEl.innerHTML = '<i class="fa-solid fa-check-circle"></i> Settings saved successfully!';
        
        // Refresh settings to get updated version info
        setTimeout(() => {
            loadSettings();
        }, 1000);
        
        // Auto hide after 3 seconds
        setTimeout(() => {
            msgEl.style.display = 'none';
        }, 5000);
        
    } catch (error) {
        console.error('Error saving settings:', error);
        msgEl.style.color = '#EF4444';
        msgEl.innerHTML = '<i class="fa-solid fa-exclamation-circle"></i> Network error. Please try again.';
    }
}

// ============ END SETTINGS FUNCTIONS ============

// ============ WORK STATUS REQUEST FUNCTIONS ============

// Load work status requests from API
async function loadWorkStatusRequests() {
    try {
        const response = await fetch(`${dashboardApiBaseUrl}/api/work-status-requests`, {
            method: 'GET',
            headers: getAuthHeaders(),
            credentials: 'include',
            cache: 'no-store'
        });

        if (response.status === 401) {
            redirectToLogin();
            return;
        }

        if (!response.ok) {
            console.error('Failed to load work status requests:', response.status);
            return;
        }

        const result = await response.json();
        if (result.status === 'success' && result.data) {
            // Normalize every record so `status` is always set, regardless of
            // which bucket the backend returned it in. The backend keeps
            // `requests` for pending, `approved` for approved, and `rejected`
            // for rejected, but older records may not carry a status field.
            const pending = (result.data.requests || []).map(r => ({
                ...r,
                status: (r.status || 'pending').toLowerCase()
            }));
            const approved = (result.data.approved || []).map(r => ({
                ...r,
                status: (r.status || 'approved').toLowerCase()
            }));
            const rejected = (result.data.rejected || []).map(r => ({
                ...r,
                status: (r.status || 'rejected').toLowerCase()
            }));

            // Store all work status requests (pending + approved + rejected)
            const allRequests = [...pending, ...approved, ...rejected];
            workStatusRequests = allRequests;
            filteredWorkStatusRequests = [...workStatusRequests];
            workStatusRequestsCurrentPage = 1;

            // Update dashboard statistics
            updateWorkStatusRequestStatistics({
                pending: pending.length,
                approved: approved.length,
                rejected: rejected.length,
                total: allRequests.length,
                // Pass the actual approved request records so the
                // "On Work Status" card can count employees on work status today.
                approvedRequests: approved
            });

            // Render work status requests table (for HR/Admin)
            renderWorkStatusRequestTable();

            // Render work status request form (for employees only)
            const userRole = (currentAdminUser && currentAdminUser.role) || 'employee';
            if (userRole !== 'admin' && userRole !== 'hr') {
                renderWorkStatusRequestForm();
            }

            // Render filter controls
            renderWorkStatusRequestFilterControls();
        }
    } catch (error) {
        console.error('Error loading work status requests:', error);
    }
}

// Update work status request statistics in dashboard
function updateWorkStatusRequestStatistics(stats) {
    const pendingEl = document.getElementById('statWorkStatusRequests');
    if (pendingEl) {
        pendingEl.textContent = stats.pending || 0;
    }

    // Update on work status stat based on approved requests
    const onWorkStatusEl = document.getElementById('statOnWorkStatus');
    if (onWorkStatusEl) {
        // Count unique employees on work status today from approved requests
        const today = new Date().toISOString().split('T')[0];
        const employeesOnWorkStatus = new Set();

        (stats.approvedRequests || []).forEach(req => {
            if (req.days && req.days.includes(today)) {
                employeesOnWorkStatus.add(req.uid);
            }
        });

        onWorkStatusEl.textContent = employeesOnWorkStatus.size;
    }
}

// Filter work status requests based on current filters
function filterWorkStatusRequests() {
    if (!workStatusRequests || workStatusRequests.length === 0) {
        filteredWorkStatusRequests = [];
        return;
    }

    filteredWorkStatusRequests = workStatusRequests.filter(req => {
        // Employee search filter (matches name or ID)
        const matchesEmployeeSearch = !workStatusRequestFilters.employeeSearch ||
            `${req.fullname || ''} ${req.employeeid || ''}`.toLowerCase().includes(
                workStatusRequestFilters.employeeSearch.toLowerCase()
            );

        return matchesEmployeeSearch;
    });

    // Reset to first page when filtering
    workStatusRequestsCurrentPage = 1;
}

// Render work status request filter controls (for HR/Admin)
function renderWorkStatusRequestFilterControls() {
    const actionsContainer = document.getElementById('workStatusRequestActions');
    if (!actionsContainer) return;

    // Check if user is HR/Admin (safe fallback to 'employee' if currentAdminUser
    // hasn't been populated yet)
    const userRole = (currentAdminUser && currentAdminUser.role) || 'employee';
    if (userRole !== 'admin' && userRole !== 'hr') {
        // Employees see the request form button
        actionsContainer.innerHTML = `
            <button class="btn btn-primary" onclick="openWorkStatusRequestModal()">
                <i class="fa-solid fa-plus"></i> Request Work Status
            </button>
        `;
        return;
    }

    // HR/Admin see the toggle button for filters
    actionsContainer.innerHTML = `
        <button class="btn btn-outline" id="workStatusRequestHideFilterBtn" onclick="toggleWorkStatusRequestFilter()">
            <i class="fa-solid fa-eye-slash"></i> Hide Filter
        </button>
    `;
}

// Render work status request form in the page
function renderWorkStatusRequestForm() {
    const formSection = document.getElementById('workStatusRequestFormSection');
    if (!formSection) return;

    // Check if user is HR/Admin or employee (everyone can submit requests)
    const userRole = (currentAdminUser && currentAdminUser.role) || 'employee';

    // Both employees and HR/Admin can see and use the work status request form
    formSection.innerHTML = `
        <form id="workStatusRequestFormPage" onsubmit="return handleWorkStatusRequestFormSubmit(event)">
            <div class="grid-2">
                <div class="form-group">
                    <label for="workStatusType">Work Status Type</label>
                    <select class="form-control" id="workStatusType" name="workStatusType" required>
                        <option value="">-- Select Work Status Type --</option>
                        <option value="on_leave">On Leave (Work Status) — approved vacation, sick, emergency, etc.</option>
                        <option value="official_travel">Official Travel — traveling for work</option>
                        <option value="official_business">Official Business — working outside the regular workplace</option>
                        <option value="work_from_home">Work From Home (WFH) — working remotely</option>
                        <option value="field_work">Field Work — assigned to work at another location</option>
                        <option value="training">Training — attending an official training/seminar</option>
                        <option value="conference_seminar">Conference / Seminar — attending an official event</option>
                        <option value="work_assignment">Work Assignment — temporarily assigned elsewhere</option>
                        <option value="offsite_duty">Offsite Duty — performing work outside the office</option>
                        <option value="client_visit">Client Visit — visiting a client or partner</option>
                        <option value="meeting_outside_office">Meeting Outside Office — attending an external meeting</option>
                        <option value="special_assignment">Special Assignment — temporary special work assignment</option>
                        <option value="suspended_work">Suspended Work — work suspended due to an official reason</option>
                        <option value="holiday_non_working">Holiday / Non-Working Day — no regular work scheduled</option>
                        <option value="rest_day">Rest Day — scheduled day off</option>
                    </select>
                </div>
                <div class="form-group">
                    <label for="workStatusPeriod">Time Period</label>
                    <select class="form-control" id="workStatusPeriod" name="workStatusPeriod" required>
                        <option value="whole_day">Whole Day</option>
                        <option value="am">Morning (AM) Only</option>
                        <option value="pm">Afternoon (PM) Only</option>
                    </select>
                </div>
            </div>
            <div class="grid-2">
                <div class="form-group">
                    <label for="workStatusStartDate">Start Date</label>
                    <input class="form-control" type="date" id="workStatusStartDate" name="startDate" required>
                </div>
                <div class="form-group">
                    <label for="workStatusEndDate">End Date</label>
                    <input class="form-control" type="date" id="workStatusEndDate" name="endDate" required>
                </div>
            </div>
            <div class="form-group">
                <label for="workStatusReason">Reason</label>
                <textarea class="form-control" id="workStatusReason" name="reason" rows="4" placeholder="Please provide a brief reason for your work status request..." required></textarea>
            </div>
            <div class="form-group">
                <label for="workStatusAttachment">Attachment (Optional)</label>
                <input class="form-control" type="file" id="workStatusAttachment" name="attachment" accept=".pdf,.doc,.docx,.jpg,.jpeg,.png,.gif,.webp,.txt">
                <small class="form-text text-muted">Allowed formats: PDF, DOC, DOCX, JPG, JPEG, PNG, GIF, WEBP, TXT (Max 5MB)</small>
            </div>
            <div id="workStatusRequestMessagePage" style="margin-top:16px;display:none;"></div>
            <div style="display:flex;justify-content:center;margin-top:24px;">
                <button type="submit" class="btn btn-primary">
                    <i class="fa-solid fa-paper-plane"></i> Submit Request
                </button>
            </div>
        </form>
    `;
}

// Clear all work status request filters
function clearWorkStatusRequestFilters() {
    workStatusRequestFilters = {
        employeeSearch: ''
    };
    filterWorkStatusRequests();
}

// Toggle work status request filter visibility
function toggleWorkStatusRequestFilter() {
    const filterArea = document.getElementById('workStatusRequestFilterArea');
    const btn = document.getElementById('workStatusRequestHideFilterBtn');
    if (!filterArea) return;
    const isVisible = filterArea.style.display !== 'none';
    filterArea.style.display = isVisible ? 'none' : 'block';
    if (btn) {
        btn.innerHTML = isVisible
            ? '<i class="fa-solid fa-eye"></i> Show Filter'
            : '<i class="fa-solid fa-eye-slash"></i> Hide Filter';
    }
}

// Render work status requests table (for HR/Admin)
function renderWorkStatusRequestTable() {
    const tableSection = document.getElementById('workStatusRequestTableSection');
    if (!tableSection) return;

    // Check if user is HR/Admin
    const userRole = (currentAdminUser && currentAdminUser.role) || 'employee';
    if (userRole !== 'admin' && userRole !== 'hr') {
        // Employees don't see the management table
        tableSection.innerHTML = '';
        return;
    }

    // Apply filters first
    filterWorkStatusRequests();

    // Filter to only show pending requests for management (if no status filter is set)
    let displayRequests = [...filteredWorkStatusRequests];

    // Apply sorting
    displayRequests.sort((a, b) => {
        let valueA = a[workStatusRequestSort.field];
        let valueB = b[workStatusRequestSort.field];

        // Handle the "employee" pseudo-field — sort by fullname instead
        // because req.employee doesn't exist on the record.
        if (workStatusRequestSort.field === 'employee') {
            valueA = a.fullname || '';
            valueB = b.fullname || '';
        }

        // Handle date fields
        if (workStatusRequestSort.field.includes('date')) {
            valueA = new Date(valueA);
            valueB = new Date(valueB);
        }

        if (valueA < valueB) return workStatusRequestSort.direction === 'asc' ? -1 : 1;
        if (valueA > valueB) return workStatusRequestSort.direction === 'asc' ? 1 : -1;
        return 0;
    });

    if (displayRequests.length === 0) {
        tableSection.innerHTML = `
            <div class="card">
                <div class="card-header">
                    <div class="card-title"><i class="fa-solid fa-briefcase"></i> Work Status Requests</div>
                </div>
                <div class="card-body">
                    <p class="text-center">No work status requests matching your filters.</p>
                </div>
            </div>
        `;
        return;
    }

    // Calculate pagination
    const totalPages = Math.ceil(displayRequests.length / WORK_STATUS_REQUESTS_PER_PAGE);
    const startIndex = (workStatusRequestsCurrentPage - 1) * WORK_STATUS_REQUESTS_PER_PAGE;
    const endIndex = Math.min(startIndex + WORK_STATUS_REQUESTS_PER_PAGE, displayRequests.length);
    const pageRequests = displayRequests.slice(startIndex, endIndex);

    tableSection.innerHTML = `
        <div class="card">
            <div class="card-header">
                <div class="card-title"><i class="fa-solid fa-briefcase"></i> Work Status Requests (${filteredWorkStatusRequests.filter(r => r.status === 'pending').length} pending)</div>
            </div>
            <div class="card-body">
                <div class="table-wrap">
                    <table class="data-table">
                        <thead>
                            <tr>
                                <th onclick="sortWorkStatusRequests('employee')">
                                    Employee
                                    ${workStatusRequestSort.field === 'employee' && workStatusRequestSort.direction === 'asc' ? ' ↑' :
                                     workStatusRequestSort.field === 'employee' && workStatusRequestSort.direction === 'desc' ? ' ↓' : ''}
                                </th>
                                <th onclick="sortWorkStatusRequests('work_status_type')">
                                    Work Status Type
                                    ${workStatusRequestSort.field === 'work_status_type' && workStatusRequestSort.direction === 'asc' ? ' ↑' :
                                     workStatusRequestSort.field === 'work_status_type' && workStatusRequestSort.direction === 'desc' ? ' ↓' : ''}
                                </th>
                                <th onclick="sortWorkStatusRequests('start_date')">
                                    Dates
                                    ${workStatusRequestSort.field === 'start_date' && workStatusRequestSort.direction === 'asc' ? ' ↑' :
                                     workStatusRequestSort.field === 'start_date' && workStatusRequestSort.direction === 'desc' ? ' ↓' : ''}
                                </th>
                                <th>Period</th>
                                <th>Days</th>
                                <th onclick="sortWorkStatusRequests('status')">
                                    Status
                                    ${workStatusRequestSort.field === 'status' && workStatusRequestSort.direction === 'asc' ? ' ↑' :
                                     workStatusRequestSort.field === 'status' && workStatusRequestSort.direction === 'desc' ? ' ↓' : ''}
                                </th>
                                <th>Actions</th>
                            </tr>
                        </thead>
                        <tbody>
                            ${pageRequests.map(req => {
                                const startDate = new Date(req.start_date);
                                const endDate = new Date(req.end_date);
                                const formattedStart = startDate.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
                                const formattedEnd = endDate.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
                                const dateRange = formattedStart === formattedEnd ? formattedStart : `${formattedStart} - ${formattedEnd}`;

                                // Calculate working days (excluding weekends)
                                const workDays = req.days ? req.days.filter(day => {
                                    const date = new Date(day);
                                    return date.getDay() !== 0 && date.getDay() !== 6; // Not Sunday (0) or Saturday (6)
                                }).length : 0;

                                // Get work status label
                                const wsLabel = req.work_status_label || (req.work_status_type || '').replace(/_/g, ' ').replace(/\b\w/g, l => l.toUpperCase());

                                // Optional attachment link — the backend saves it under
                                // storage/work-status/<RFID>/<filename> and returns the
                                // relative path in req.attachment_path.
                                const attachmentLink = req.attachment_path
                                    ? `<div style="margin-top:4px;font-size:11px;">
                                         <a href="javascript:void(0)" onclick="openWorkStatusAttachmentModal('${escapeHtml(req.attachment_path)}', '${escapeHtml(req.attachment_path.split('/').pop() || 'attachment')}')" style="color:var(--primary);text-decoration:underline;">
                                           <i class="fa-solid fa-paperclip"></i> Attachment
                                         </a>
                                       </div>`
                                    : '';
                                return `
                                    <tr>
                                        <td>
                                            <div class="emp-info">
                                                <div>${escapeHtml(req.fullname)}</div>
                                                <small class="text-muted">${escapeHtml(req.employeeid || req.uid)}</small>
                                                ${attachmentLink}
                                            </div>
                                        </td>
                                        <td>
                                            <span class="badge" style="background:var(--leave-light);color:var(--leave);">
                                                ${escapeHtml(wsLabel)}
                                            </span>
                                        </td>
                                        <td>${dateRange}</td>
                                        <td>${(req.period || 'whole_day').replace('_', ' ').toUpperCase()}</td>
                                        <td>${workDays}</td>
                                        <td>
                                            <span class="badge ${req.status === 'approved' ? 'badge-approved' :
                                                            req.status === 'rejected' ? 'badge-rejected' : 'badge-pending'}">
                                                ${escapeHtml((req.status || '').toUpperCase())}
                                            </span>
                                        </td>
                                        <td>
                                            <div class="action-buttons" style="display:flex;gap:4px;flex-wrap:wrap;">
                                                <button class="btn btn-outline btn-sm" onclick="openWorkStatusDetailModal('${req.id}')">
                                                    <i class="fa-solid fa-eye"></i> View
                                                </button>
                                                ${req.status === 'pending' ? `
                                                    <button class="btn btn-outline btn-sm" onclick="openApproveWorkStatusModal('${req.id}')">
                                                        <i class="fa-solid fa-check"></i> Approve
                                                    </button>
                                                    <button class="btn btn-outline btn-sm" onclick="rejectWorkStatusRequest('${req.id}')">
                                                        <i class="fa-solid fa-times"></i> Reject
                                                    </button>
                                                ` : ''}
                                            </div>
                                        </td>
                                    </tr>
                                `;
                            }).join('')}
                        </tbody>
                    </table>
                </div>
            </div>
        </div>
    `;

    // Add pagination controls
    const paginationContainer = document.createElement('div');
    paginationContainer.className = 'pagination-footer';
    paginationContainer.style.display = 'flex';
    paginationContainer.style.justifyContent = 'center';
    paginationContainer.style.alignItems = 'center';
    paginationContainer.style.gap = '8px';
    paginationContainer.style.marginTop = '16px';

    let paginationHTML = '';

    // Previous button
    paginationHTML += `
        <button class="btn btn-outline btn-sm pagination-btn"
                onclick="changeWorkStatusRequestPage(${workStatusRequestsCurrentPage - 1})"
                ${workStatusRequestsCurrentPage <= 1 ? 'disabled style="opacity:0.5;cursor:not-allowed;"' : ''}>
            Prev
        </button>
    `;

    // Page numbers
    const maxVisiblePages = 5;
    let startPage = Math.max(1, workStatusRequestsCurrentPage - Math.floor(maxVisiblePages / 2));
    let endPage = Math.min(totalPages, startPage + maxVisiblePages - 1);

    if (endPage - startPage < maxVisiblePages - 1) {
        startPage = Math.max(1, endPage - maxVisiblePages + 1);
    }

    if (startPage > 1) {
        paginationHTML += `<button class="btn btn-outline btn-sm pagination-btn" onclick="changeWorkStatusRequestPage(1)">1</button>`;
        if (startPage > 2) {
            paginationHTML += `<span style="color:var(--text-muted);padding:0 4px;">…</span>`;
        }
    }

    for (let i = startPage; i <= endPage; i++) {
        const isActive = i === workStatusRequestsCurrentPage;
        paginationHTML += `
            <button class="btn ${isActive ? 'btn-primary' : 'btn-outline'} btn-sm pagination-btn"
                    onclick="changeWorkStatusRequestPage(${i})"
                    ${isActive ? 'style="font-weight:700;"' : ''}>
                ${i}
            </button>
        `;
    }

    if (endPage < totalPages) {
        if (endPage < totalPages - 1) {
            paginationHTML += `<span style="color:var(--text-muted);padding:0 4px;">…</span>`;
        }
        paginationHTML += `<button class="btn btn-outline btn-sm pagination-btn"
                                onclick="changeWorkStatusRequestPage(${totalPages})">${totalPages}</button>`;
    }

    // Next button
    paginationHTML += `
        <button class="btn btn-outline btn-sm pagination-btn"
                onclick="changeWorkStatusRequestPage(${workStatusRequestsCurrentPage + 1})"
                ${workStatusRequestsCurrentPage >= totalPages ? 'disabled style="opacity:0.5;cursor:not-allowed;"' : ''}>
            Next
        </button>
    `;

    paginationContainer.innerHTML = paginationHTML;
    tableSection.appendChild(paginationContainer);
}

// Change work status request page
function changeWorkStatusRequestPage(page) {
    const totalPages = Math.ceil(filteredWorkStatusRequests.length / WORK_STATUS_REQUESTS_PER_PAGE);
    if (page < 1 || page > totalPages) return;
    workStatusRequestsCurrentPage = page;
    renderWorkStatusRequestTable();
}

// Sort work status requests by field
function sortWorkStatusRequests(field) {
    // Toggle direction if clicking the same field
    if (workStatusRequestSort.field === field) {
        workStatusRequestSort.direction = workStatusRequestSort.direction === 'asc' ? 'desc' : 'asc';
    } else {
        // Sort ascending by default for new field
        workStatusRequestSort.field = field;
        workStatusRequestSort.direction = 'asc';
    }

    // Reset to first page when sorting
    workStatusRequestsCurrentPage = 1;
    renderWorkStatusRequestTable();
}

/* ============================================================================
 * APPROVE WORK STATUS MODAL — lets HR/Admin choose WHOLE DAY, AM ONLY, or
 * PM ONLY and optionally enter specific start/end times at approval time.
 *
 * Why this exists: an employee might submit a whole-day work status request,
 * but HR/Admin may only want to approve the morning portion (or vice versa).
 * When a specific period is chosen here, the backend stores `period` on the
 * DTR day so only the affected period gets highlighted yellow — not the whole
 * day. This keeps the DTR accurate.
 * ==========================================================================*/
function openApproveWorkStatusModal(requestId) {
    const req = workStatusRequests.find(r => String(r.id) === String(requestId));
    if (!req) {
        showNotification('Work status request not found.', 'error');
        return;
    }

    const wsLabel = req.work_status_label || (req.work_status_type || '').replace(/_/g, ' ').replace(/\b\w/g, l => l.toUpperCase());
    const currentPeriod = (req.period || 'whole_day');

    const modalHtml = `
        <div class="modal-overlay" id="approveWorkStatusModal" onclick="if(event.target===this) closeApproveWorkStatusModal()">
            <div class="modal-content edit-modal" onclick="event.stopPropagation()" style="max-width:560px;">
                <div class="modal-header" style="background:linear-gradient(135deg,#10B981,#34D399);">
                    <div class="modal-avatar"><i class="fa-solid fa-check-circle" style="font-size:22px;"></i></div>
                    <div class="modal-user-info">
                        <h2>Approve Work Status</h2>
                        <span style="font-size:13px;opacity:0.85;">${escapeHtml(req.fullname || 'Unknown')} · ${escapeHtml(wsLabel)}</span>
                    </div>
                    <button class="modal-close" onclick="closeApproveWorkStatusModal()">&times;</button>
                </div>
                <div class="modal-body">
                    <p style="font-size:13px;color:var(--text-muted);margin-bottom:16px;">
                        You can approve the whole day, or scope the approval to only the
                        <strong>Morning (AM)</strong> or only the <strong>Afternoon (PM)</strong> portion
                        so the DTR highlight appears only on the affected period.
                    </p>

                    <div class="form-group">
                        <label>Approve For</label>
                        <select class="form-control" id="approveWorkStatusPeriod">
                            <option value="whole_day" ${currentPeriod === 'whole_day' ? 'selected' : ''}>Whole Day</option>
                            <option value="am" ${currentPeriod === 'am' ? 'selected' : ''}>Morning (AM) Only</option>
                            <option value="pm" ${currentPeriod === 'pm' ? 'selected' : ''}>Afternoon (PM) Only</option>
                        </select>
                    </div>

                    <div class="grid-2">
                        <div class="form-group">
                            <label>Specific Start Time (Optional)</label>
                            <input class="form-control" type="time" id="approveWorkStatusStartTime" value="${escapeHtml(req.start_time || '')}">
                        </div>
                        <div class="form-group">
                            <label>Specific End Time (Optional)</label>
                            <input class="form-control" type="time" id="approveWorkStatusEndTime" value="${escapeHtml(req.end_time || '')}">
                        </div>
                    </div>

                    <div style="background:var(--bg);border:1px solid var(--border);border-radius:8px;padding:12px;margin-top:12px;font-size:12px;color:var(--text-muted);">
                        <strong style="color:var(--text);">Dates:</strong>
                        ${escapeHtml(req.start_date || '')} → ${escapeHtml(req.end_date || '')}
                        <br>
                        <strong style="color:var(--text);">Reason:</strong>
                        ${escapeHtml(req.reason || '—')}
                    </div>

                    <div id="approveWorkStatusMessage" style="margin-top:12px;font-size:13px;display:none;"></div>
                </div>
                <div class="modal-footer" style="display:flex;justify-content:flex-end;gap:8px;">
                    <button type="button" class="btn btn-outline" onclick="closeApproveWorkStatusModal()">
                        <i class="fa-solid fa-times"></i> Cancel
                    </button>
                    <button type="button" class="btn btn-primary" onclick="confirmApproveWorkStatus('${escapeHtml(req.id)}')">
                        <i class="fa-solid fa-check"></i> Confirm Approval
                    </button>
                </div>
            </div>
        </div>
    `;

    const existing = document.getElementById('approveWorkStatusModal');
    if (existing) existing.remove();
    document.body.insertAdjacentHTML('beforeend', modalHtml);
    document.body.style.overflow = 'hidden';
}

function closeApproveWorkStatusModal() {
    const modal = document.getElementById('approveWorkStatusModal');
    if (modal) modal.remove();
    document.body.style.overflow = '';
}

/* Send the approval to the backend with the chosen period and specific times.
   The backend stores the period on the DTR day so only that period is
   highlighted yellow. */
async function confirmApproveWorkStatus(requestId) {
    const period = document.getElementById('approveWorkStatusPeriod')?.value || 'whole_day';
    const startTime = document.getElementById('approveWorkStatusStartTime')?.value || '';
    const endTime = document.getElementById('approveWorkStatusEndTime')?.value || '';

    const msgEl = document.getElementById('approveWorkStatusMessage');
    if (msgEl) {
        msgEl.style.display = 'block';
        msgEl.style.color = '#3B82F6';
        msgEl.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i> Approving...';
    }

    try {
        const response = await fetch(`${dashboardApiBaseUrl}/api/approve-work-status/${encodeURIComponent(requestId)}`, {
            method: 'POST',
            headers: getAuthHeaders(),
            body: JSON.stringify({
                period: period,
                start_time: startTime,
                end_time: endTime
            }),
            credentials: 'include'
        });

        if (response.status === 401) {
            redirectToLogin();
            return;
        }

        const result = await response.json().catch(() => ({}));
        if (!response.ok) {
            if (msgEl) {
                msgEl.style.color = '#EF4444';
                msgEl.innerHTML = `<i class="fa-solid fa-exclamation-circle"></i> ${result.message || 'Failed to approve request.'}`;
            }
            return;
        }

        if (msgEl) {
            msgEl.style.color = '#10B981';
            msgEl.innerHTML = '<i class="fa-solid fa-check-circle"></i> Approved successfully!';
        }

        showNotification('Work status request approved!', 'success');
        setTimeout(() => {
            closeApproveWorkStatusModal();
            loadWorkStatusRequests();
        }, 800);
    } catch (error) {
        console.error('Error approving work status request:', error);
        if (msgEl) {
            msgEl.style.color = '#EF4444';
            msgEl.innerHTML = '<i class="fa-solid fa-exclamation-circle"></i> Network error. Please try again.';
        }
    }
}

// Approve work status request (legacy — used when "Approve" is clicked
// directly without the period modal; still keeps the request's original period)
async function approveWorkStatusRequest(requestId) {
    if (!confirm('Approve this work status request?')) return;

    try {
        // The backend route is POST /api/approve-work-status/<request_id>
        const response = await fetch(`${dashboardApiBaseUrl}/api/approve-work-status/${encodeURIComponent(requestId)}`, {
            method: 'POST',
            headers: getAuthHeaders(),
            credentials: 'include'
        });

        if (response.status === 401) {
            redirectToLogin();
            return;
        }

        if (!response.ok) {
            const err = await response.json().catch(() => ({}));
            showNotification(err.message || 'Failed to approve work status request.', 'error');
            return;
        }

        showNotification('Work status request approved successfully!', 'success');
        await loadWorkStatusRequests();
    } catch (error) {
        console.error('Error approving work status request:', error);
        showNotification('Network error. Please try again.', 'error');
    }
}

// Reject work status request
async function rejectWorkStatusRequest(requestId) {
    if (!confirm('Reject this work status request?')) return;

    try {
        // The backend route is POST /api/reject-work-status/<request_id>
        const response = await fetch(`${dashboardApiBaseUrl}/api/reject-work-status/${encodeURIComponent(requestId)}`, {
            method: 'POST',
            headers: getAuthHeaders(),
            credentials: 'include'
        });

        if (response.status === 401) {
            redirectToLogin();
            return;
        }

        if (!response.ok) {
            const err = await response.json().catch(() => ({}));
            showNotification(err.message || 'Failed to reject work status request.', 'error');
            return;
        }

        showNotification('Work status request rejected successfully!', 'success');
        await loadWorkStatusRequests();
    } catch (error) {
        console.error('Error rejecting work status request:', error);
        showNotification('Network error. Please try again.', 'error');
    }
}

// Open work status request modal (for employees)
function openWorkStatusRequestModal() {
    const modal = document.getElementById('workStatusRequestModal');
    if (modal) {
        modal.classList.remove('hidden');
        modal.style.display = 'flex';
        const form = document.getElementById('workStatusRequestForm');
        if (form) form.reset();
    }
}

// Close work status request modal
function closeWorkStatusRequestModal() {
    const modal = document.getElementById('workStatusRequestModal');
    if (modal) {
        modal.classList.add('hidden');
        modal.style.display = 'none';
    }
}

// Handle work status request form submission
async function handleWorkStatusRequestFormSubmit(event) {
    event.preventDefault();

    const form = event.target;

    // Read fields by id — FormData only captures inputs that have `name`
    // attributes, and these inputs use `id` only. So read directly.
    const workStatusType = document.getElementById('workStatusType').value;
    const workStatusPeriod = document.getElementById('workStatusPeriod').value;
    const startDate = document.getElementById('workStatusStartDate').value;
    const endDate = document.getElementById('workStatusEndDate').value;
    const reason = document.getElementById('workStatusReason').value.trim();
    const attachmentFile = document.getElementById('workStatusAttachment').files[0];

    // Basic validation
    if (!workStatusType || !startDate || !endDate || !reason) {
        showWorkStatusRequestMessage('Please fill in all required fields.', 'error');
        return;
    }

    // Validate dates are not in the past
    const startDateObj = new Date(startDate);
    const endDateObj = new Date(endDate);
    const today = new Date();
    today.setHours(0, 0, 0, 0); // Set to midnight to compare dates only

    if (startDateObj < today) {
        showWorkStatusRequestMessage('Work status requests cannot be submitted for past dates.', 'error');
        return;
    }

    if (endDateObj < today) {
        showWorkStatusRequestMessage('Work status requests cannot be submitted for past dates.', 'error');
        return;
    }

    if (startDateObj > endDateObj) {
        showWorkStatusRequestMessage('Start date cannot be after end date.', 'error');
        return;
    }

    // Validate attachment if provided
    if (attachmentFile) {
        // FIXED: this MUST be an Array (square brackets) — not an object literal.
        // Objects do not have an .includes() method, so the old `{...}` version
        // would throw "allowedExtensions.includes is not a function" at runtime.
        const allowedExtensions = ['.pdf', '.doc', '.docx', '.jpg', '.jpeg', '.png', '.gif', '.webp', '.txt'];
        const extension = attachmentFile.name.substring(attachmentFile.name.lastIndexOf('.')).toLowerCase();
        if (!allowedExtensions.includes(extension)) {
            showWorkStatusRequestMessage('File must be PDF, DOC, DOCX, JPG, JPEG, PNG, GIF, WEBP, or TXT', 'error');
            return;
        }

        // Check file size (5MB limit)
        if (attachmentFile.size > 5 * 1024 * 1024) {
            showWorkStatusRequestMessage('File size must be less than 5MB', 'error');
            return;
        }
    }

    // Get current user's RFID
    const rfid = currentAdminUser ? currentAdminUser.rfid : '';
    if (!rfid) {
        showWorkStatusRequestMessage('Unable to identify employee. Please log in again.', 'error');
        return;
    }

    // Show loading state
    showWorkStatusRequestMessage('Submitting work status request...', 'info');

    try {
        // Prepare form data for API
        const apiFormData = new FormData();
        apiFormData.append('rfid', rfid);
        apiFormData.append('start_date', startDate);
        apiFormData.append('end_date', endDate);
        apiFormData.append('reason', reason);
        apiFormData.append('work_status_type', workStatusType);
        apiFormData.append('period', workStatusPeriod);

        if (attachmentFile) {
            apiFormData.append('attachment', attachmentFile);
        }

        const response = await fetch(`${dashboardApiBaseUrl}/api/request-work-status`, {
            method: 'POST',
            headers: {
                'Authorization': `Bearer ${localStorage.getItem('tapinToken')}`
                // Don't set Content-Type for FormData
            },
            body: apiFormData,
            credentials: 'include'
        });

        const result = await response.json();

        if (response.status === 401) {
            redirectToLogin();
            return;
        }

        if (!response.ok) {
            showWorkStatusRequestMessage(result.message || 'Failed to submit work status request.', 'error');
            return;
        }

        // Success
        showWorkStatusRequestMessage('Work status request submitted successfully!', 'success');

        // Reset form
        form.reset();
        document.getElementById('workStatusAttachment').value = '';

        // Reload work status requests to update the list
        setTimeout(() => {
            loadWorkStatusRequests();
        }, 1000);

    } catch (error) {
        console.error('Error submitting work status request:', error);
        showWorkStatusRequestMessage('Network error. Please try again.', 'error');
    }
}

// Show work status request form message
function showWorkStatusRequestMessage(message, type = 'info') {
    const msgEl = document.getElementById('workStatusRequestMessage') || document.getElementById('workStatusRequestMessagePage');
    if (!msgEl) return;

    const colors = {
        success: '#10B981',
        error: '#EF4444',
        warning: '#F59E0B',
        info: '#3B82F6'
    };

    const icons = {
        success: 'fa-check-circle',
        error: 'fa-exclamation-circle',
        warning: 'fa-exclamation-triangle',
        info: 'fa-info-circle'
    };

    msgEl.innerHTML = `<i class="fa-solid ${icons[type] || icons.info}"></i> ${message}`;
    msgEl.style.color = colors[type] || colors.info;
    msgEl.style.display = 'block';

    // Auto hide after 5 seconds for success messages
    if (type === 'success') {
        setTimeout(() => {
            msgEl.style.display = 'none';
        }, 5000);
    }
}

// ============ END WORK STATUS REQUEST FUNCTIONS ============

// ============ WORK STATUS DETAIL MODAL ============

// Open the work status detail modal for a specific request ID.
// Shows every field stored on the record and, if the request has an
// attachment, renders an inline preview (image / PDF / text) plus
// "Open in New Tab" and "Download" buttons.
function openWorkStatusDetailModal(requestId) {
    const req = workStatusRequests.find(r => String(r.id) === String(requestId));
    if (!req) {
        showNotification('Work status request not found.', 'error');
        return;
    }

    const modal = document.getElementById('workStatusDetailModal');
    if (!modal) return;

    // Populate header
    const titleEl = document.getElementById('workStatusDetailTitle');
    const subtitleEl = document.getElementById('workStatusDetailSubtitle');
    if (titleEl) titleEl.textContent = `Work Status Request #${escapeHtml(req.id)}`;
    if (subtitleEl) subtitleEl.textContent = `${escapeHtml(req.fullname || 'Unknown')} · ${escapeHtml((req.work_status_label || req.work_status_type || '').toUpperCase())}`;

    // Build the detail body
    const body = document.getElementById('workStatusDetailBody');
    if (!body) return;

    const statusColors = {
        pending: { bg: 'var(--warning-light)', color: 'var(--warning)' },
        approved: { bg: 'var(--success-light)', color: 'var(--success)' },
        rejected: { bg: 'var(--danger-light)', color: 'var(--danger)' }
    };
    const sc = statusColors[req.status] || statusColors.pending;

    const startDate = req.start_date ? new Date(req.start_date) : null;
    const endDate = req.end_date ? new Date(req.end_date) : null;
    const formattedStart = startDate ? startDate.toLocaleDateString('en-US', { month: 'long', day: 'numeric', year: 'numeric' }) : 'N/A';
    const formattedEnd = endDate ? endDate.toLocaleDateString('en-US', { month: 'long', day: 'numeric', year: 'numeric' }) : 'N/A';

    // Count working days (Mon-Fri) from the days array
    const workDays = (req.days || []).filter(day => {
        const d = new Date(day);
        return d.getDay() !== 0 && d.getDay() !== 6;
    }).length;

    const wsLabel = req.work_status_label || (req.work_status_type || '').replace(/_/g, ' ').replace(/\b\w/g, l => l.toUpperCase());
    const periodLabel = (req.period || 'whole_day').replace('_', ' ').toUpperCase();

    // Render attachment area if present
    let attachmentHTML = '';
    if (req.attachment_path) {
        const fileName = req.attachment_path.split('/').pop();
        const safePath = escapeHtml(req.attachment_path);
        const inlineUrl = `${dashboardApiBaseUrl}/${req.attachment_path}?inline=1`;
        const downloadUrl = `${dashboardApiBaseUrl}/${req.attachment_path}`;

        attachmentHTML = `
            <div style="margin-top:16px;padding:12px;border:1px solid var(--border);border-radius:8px;background:var(--bg);">
                <div style="display:flex;align-items:center;justify-content:space-between;gap:8px;flex-wrap:wrap;">
                    <div style="display:flex;align-items:center;gap:8px;font-size:13px;">
                        <i class="fa-solid fa-paperclip" style="color:var(--primary);"></i>
                        <strong>${escapeHtml(fileName)}</strong>
                    </div>
                    <div style="display:flex;gap:6px;">
                        <a href="${inlineUrl}" target="_blank" rel="noopener" class="btn btn-outline btn-sm">
                            <i class="fa-solid fa-up-right-from-square"></i> Open in New Tab
                        </a>
                        <a href="${downloadUrl}" download class="btn btn-primary btn-sm">
                            <i class="fa-solid fa-download"></i> Download
                        </a>
                    </div>
                </div>
                <div id="workStatusDetailAttachmentPreview" style="margin-top:12px;min-height:120px;">
                    <div style="display:flex;justify-content:center;align-items:center;padding:40px 0;color:var(--text-muted);font-size:13px;">
                        <i class="fa-solid fa-spinner fa-spin" style="margin-right:8px;"></i> Loading preview…
                    </div>
                </div>
            </div>
        `;
    } else {
        attachmentHTML = `
            <div style="margin-top:16px;padding:12px;border:1px dashed var(--border);border-radius:8px;text-align:center;color:var(--text-muted);font-size:13px;">
                <i class="fa-solid fa-paperclip" style="margin-right:6px;"></i> No attachment provided.
            </div>
        `;
    }

    body.innerHTML = `
        <div class="detail-grid" style="display:grid;grid-template-columns:1fr 1fr;gap:12px;">
            <div class="detail-item">
                <span class="detail-label"><i class="fa-solid fa-user"></i> Employee</span>
                <span class="detail-value">${escapeHtml(req.fullname || 'N/A')}</span>
            </div>
            <div class="detail-item">
                <span class="detail-label"><i class="fa-solid fa-id-badge"></i> Employee ID</span>
                <span class="detail-value">${escapeHtml(req.employeeid || req.uid || 'N/A')}</span>
            </div>
            <div class="detail-item">
                <span class="detail-label"><i class="fa-solid fa-building"></i> Department</span>
                <span class="detail-value">${escapeHtml(req.department || 'N/A')}</span>
            </div>
            <div class="detail-item">
                <span class="detail-label"><i class="fa-solid fa-briefcase"></i> Work Status Type</span>
                <span class="detail-value" style="text-transform:capitalize;">${escapeHtml(wsLabel)}</span>
            </div>
            <div class="detail-item">
                <span class="detail-label"><i class="fa-solid fa-clock"></i> Time Period</span>
                <span class="detail-value">${escapeHtml(periodLabel)}</span>
            </div>
            <div class="detail-item">
                <span class="detail-label"><i class="fa-solid fa-calendar-plus"></i> Start Date</span>
                <span class="detail-value">${escapeHtml(formattedStart)}</span>
            </div>
            <div class="detail-item">
                <span class="detail-label"><i class="fa-solid fa-calendar-minus"></i> End Date</span>
                <span class="detail-value">${escapeHtml(formattedEnd)}</span>
            </div>
            <div class="detail-item">
                <span class="detail-label"><i class="fa-solid fa-clock"></i> Working Days</span>
                <span class="detail-value">${workDays} day${workDays !== 1 ? 's' : ''}</span>
            </div>
            <div class="detail-item">
                <span class="detail-label"><i class="fa-solid fa-circle-info"></i> Status</span>
                <span class="detail-value">
                    <span class="badge" style="background:${sc.bg};color:${sc.color};">
                        ${escapeHtml((req.status || '').toUpperCase())}
                    </span>
                </span>
            </div>
            <div class="detail-item" style="grid-column:1/-1;">
                <span class="detail-label"><i class="fa-solid fa-comment-dots"></i> Reason</span>
                <span class="detail-value" style="white-space:pre-wrap;">${escapeHtml(req.reason || 'No reason provided.')}</span>
            </div>
            <div class="detail-item">
                <span class="detail-label"><i class="fa-solid fa-calendar-day"></i> Requested At</span>
                <span class="detail-value">${req.requested_at ? new Date(req.requested_at).toLocaleString() : 'N/A'}</span>
            </div>
            <div class="detail-item">
                <span class="detail-label"><i class="fa-solid fa-user-check"></i> Processed By</span>
                <span class="detail-value">${escapeHtml(req.processed_by || '—')}</span>
            </div>
            ${req.processed_at ? `
            <div class="detail-item">
                <span class="detail-label"><i class="fa-solid fa-clock-rotate-left"></i> Processed At</span>
                <span class="detail-value">${new Date(req.processed_at).toLocaleString()}</span>
            </div>` : ''}
        </div>
        ${attachmentHTML}
    `;

    // Populate footer actions
    const footerLeft = document.getElementById('workStatusDetailFooterLeft');
    if (footerLeft) {
        if (req.status === 'pending') {
            footerLeft.innerHTML = `
                <button class="btn btn-outline btn-sm" onclick="closeWorkStatusDetailModal(); openApproveWorkStatusModal('${escapeHtml(req.id)}');">
                    <i class="fa-solid fa-check"></i> Approve
                </button>
                <button class="btn btn-outline btn-sm" onclick="closeWorkStatusDetailModal(); rejectWorkStatusRequest('${escapeHtml(req.id)}');">
                    <i class="fa-solid fa-times"></i> Reject
                </button>
            `;
        } else {
            footerLeft.innerHTML = '';
        }
    }

    // Show modal
    modal.classList.remove('hidden');
    document.body.style.overflow = 'hidden';

    // If there's an attachment, load its preview
    if (req.attachment_path) {
        loadWorkStatusAttachmentPreview(req.attachment_path, 'workStatusDetailAttachmentPreview');
    }
}

// Close the work status detail modal
function closeWorkStatusDetailModal() {
    const modal = document.getElementById('workStatusDetailModal');
    if (modal) modal.classList.add('hidden');
    document.body.style.overflow = '';
}

// Load and render an inline preview of a work status attachment into a given
// container element ID. Uses the /api/work-status-attachment/meta endpoint to
// decide how to render (image, PDF, text) and falls back to a download
// card for anything the browser can't preview natively.
async function loadWorkStatusAttachmentPreview(attachmentPath, containerId) {
    const container = document.getElementById(containerId);
    if (!container) return;

    const fileName = attachmentPath.split('/').pop();
    const rfid = attachmentPath.split('/').slice(-2, -1)[0] || '';
    const inlineUrl = `${dashboardApiBaseUrl}/${attachmentPath}?inline=1`;

    // Determine the file extension to decide the preview type
    const ext = (fileName.split('.').pop() || '').toLowerCase();
    const imageExts = ['jpg', 'jpeg', 'png', 'gif', 'webp'];
    const textExts = ['txt'];

    if (imageExts.includes(ext)) {
        // Image preview
        container.innerHTML = `
            <div style="text-align:center;max-width:100%;overflow:auto;">
                <img src="${inlineUrl}" alt="${escapeHtml(fileName)}"
                     style="max-width:100%;max-height:200px;border-radius:8px;box-shadow:0 2px 8px rgba(0,0,0,0.1);"
                     onerror="this.parentElement.innerHTML='<div style=\\'padding:20px;color:var(--text-muted);\\'><i class=\\'fa-solid fa-image\\'></i> Could not load image preview.</div>';" />
            </div>
        `;
    } else if (ext === 'pdf') {
        // PDF preview via iframe
        container.innerHTML = `
            <iframe src="${inlineUrl}" style="width:100%;height:200px;border:1px solid var(--border);border-radius:8px;" title="PDF Preview"></iframe>
        `;
    } else if (textExts.includes(ext)) {
        // Text preview
        try {
            const res = await fetch(inlineUrl);
            const text = await res.text();
            container.innerHTML = `
                <pre style="background:#1e1e1e;color:#d4d4d4;padding:16px;border-radius:8px;font-family:monospace;font-size:12px;white-space:pre-wrap;max-height:400px;overflow:auto;">${escapeHtml(text)}</pre>
            `;
        } catch (err) {
            container.innerHTML = `
                <div style="padding:20px;text-align:center;color:var(--text-muted);font-size:13px;">
                    <i class="fa-solid fa-file-lines" style="font-size:24px;display:block;margin-bottom:8px;"></i>
                    Preview not available.
                </div>
            `;
        }
    } else {
        // Unknown / non-previewable file type
        container.innerHTML = `
            <div style="padding:20px;text-align:center;color:var(--text-muted);font-size:13px;">
                <i class="fa-solid fa-file" style="font-size:32px;display:block;margin-bottom:8px;color:var(--primary);"></i>
                <p><strong>${escapeHtml(fileName)}</strong></p>
                <p>Preview is not available for this file type.</p>
                <a href="${inlineUrl}" target="_blank" rel="noopener" class="btn btn-outline btn-sm" style="margin-top:8px;">
                    <i class="fa-solid fa-up-right-from-square"></i> Open in New Tab
                </a>
            </div>
        `;
    }
}

// ============ WORK STATUS ATTACHMENT PREVIEW MODAL ============

// Open the standalone work status attachment preview modal.
// This is what the small "Attachment" link in the work status table calls.
function openWorkStatusAttachmentModal(attachmentPath, fileName) {
    const modal = document.getElementById('workStatusAttachmentModal');
    if (!modal) return;

    const titleEl = document.getElementById('workStatusAttachmentTitle');
    const subtitleEl = document.getElementById('workStatusAttachmentSubtitle');
    const body = document.getElementById('workStatusAttachmentBody');
    const metaEl = document.getElementById('workStatusAttachmentMeta');
    const openBtn = document.getElementById('workStatusAttachmentOpenBtn');
    const downloadBtn = document.getElementById('workStatusAttachmentDownloadBtn');

    const inlineUrl = `${dashboardApiBaseUrl}/${attachmentPath}?inline=1`;
    const downloadUrl = `${dashboardApiBaseUrl}/${attachmentPath}`;

    if (titleEl) titleEl.textContent = `Attachment Preview`;
    if (subtitleEl) subtitleEl.textContent = escapeHtml(fileName || 'Loading…');
    if (openBtn) openBtn.href = inlineUrl;
    if (downloadBtn) downloadBtn.href = downloadUrl;

    // Show loading state
    if (body) {
        body.innerHTML = `
            <div style="display:flex;justify-content:center;align-items:center;padding:60px 0;color:var(--text-muted);font-size:13px;">
                <i class="fa-solid fa-spinner fa-spin" style="margin-right:8px;"></i> Loading preview…
            </div>
        `;
    }

    modal.classList.remove('hidden');
    document.body.style.overflow = 'hidden';

    // Render the preview based on file type
    const ext = (fileName.split('.').pop() || '').toLowerCase();
    const imageExts = ['jpg', 'jpeg', 'png', 'gif', 'webp'];

    if (imageExts.includes(ext)) {
        if (body) {
            body.innerHTML = `
                <div style="text-align:center;">
                    <img src="${inlineUrl}" alt="${escapeHtml(fileName)}"
                         style="max-width:100%;max-height:70vh;border-radius:8px;box-shadow:0 2px 8px rgba(0,0,0,0.1);"
                         onerror="this.parentElement.innerHTML='<div style=\\'padding:40px;color:var(--text-muted);text-align:center;\\'><i class=\\'fa-solid fa-image\\' style=\\'font-size:40px;margin-bottom:12px;display:block;\\'></i> Could not load image preview.</div>';" />
                </div>
            `;
        }
    } else if (ext === 'pdf') {
        if (body) {
            body.innerHTML = `
                <iframe src="${inlineUrl}" style="width:100%;height:70vh;border:1px solid var(--border);border-radius:8px;" title="PDF Preview"></iframe>
            `;
        }
    } else if (ext === 'txt') {
        fetch(inlineUrl)
            .then(res => res.text())
            .then(text => {
                if (body) {
                    body.innerHTML = `
                        <pre style="background:#1e1e1e;color:#d4d4d4;padding:16px;border-radius:8px;font-family:monospace;font-size:12px;white-space:pre-wrap;max-height:40vh;overflow:auto;text-align:left;">${escapeHtml(text)}</pre>
                    `;
                }
            })
            .catch(() => {
                if (body) {
                    body.innerHTML = `
                        <div style="padding:40px;text-align:center;color:var(--text-muted);font-size:13px;">
                            <i class="fa-solid fa-file-lines" style="font-size:40px;display:block;margin-bottom:12px;"></i>
                            Preview not available for this file type.
                        </div>
                    `;
                }
            });
    } else {
        if (body) {
            body.innerHTML = `
                <div style="padding:40px;text-align:center;color:var(--text-muted);font-size:13px;">
                    <i class="fa-solid fa-file" style="font-size:40px;display:block;margin-bottom:12px;color:var(--primary);"></i>
                    <p><strong>${escapeHtml(fileName)}</strong></p>
                    <p>Preview is not available for this file type.</p>
                    <p>Use the buttons below to open or download the file.</p>
                </div>
            `;
        }
    }

    // Fetch and show file metadata in the footer
    if (metaEl) {
        const rfid = attachmentPath.split('/').slice(-2, -1)[0] || '';
        fetch(`${dashboardApiBaseUrl}/api/work-status-attachment/meta/${encodeURIComponent(rfid)}/${encodeURIComponent(fileName)}`, {
            headers: getAuthHeaders(),
            credentials: 'include'
        })
        .then(res => res.json())
        .then(result => {
            if (result.status === 'success' && result.data) {
                metaEl.textContent = `${result.data.filename} · ${result.data.size_human}`;
            }
        })
        .catch(() => {
            metaEl.textContent = '';
        });
    }
}

// Close the standalone work status attachment preview modal
function closeWorkStatusAttachmentModal() {
    const modal = document.getElementById('workStatusAttachmentModal');
    if (modal) modal.classList.add('hidden');
    document.body.style.overflow = '';
}

// ============ END WORK STATUS ATTACHMENT PREVIEW MODAL ============

// ============ SESSION VERIFICATION ============

async function verifyDashboardSession() {
    const token = localStorage.getItem('tapinToken');
    if (!token) {
        redirectToLogin();
        return;
    }
    
    try {
        const response = await fetch(`${dashboardApiBaseUrl}/api/verify-token`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'Authorization': `Bearer ${token}`
            },
            credentials: 'include',
            cache: 'no-store'
        });
        if (!response.ok) {
            redirectToLogin();
            return;
        }
        const data = await response.json();
        currentAdminUser = data.user || null;
        updateUserDisplay(data.user);
        await loadDashboardData();
        // Load DTR data after dashboard loads
        await loadDTREmployees();
        await loadDTRMonths();
        // Load settings (which auto-checks version)
        await loadSettings();
        // Load work status requests
        await loadWorkStatusRequests();
        // Initialize search functionality
        initSearch();
        // Inject the DTR search dropdown styles so suggestion boxes float
        // correctly above the cards (fixes the "dropdown doesn't show" bug).
        ensureDTRSearchStyles();

        // Prime the daily stats cards immediately on session start so the
        // Present Today, RFID Scans, and Facial Scans numbers are correct
        // from the very first render — even before the next 3-second poll.
        loadDailyStats();
    } catch (error) {
        redirectToLogin();
    }
}

// ============ END SESSION VERIFICATION ============

// ============ SIDEBAR / NAV / LOGOUT ============

const sidebar = document.querySelector('.sidebar');
const sidebarToggle = document.getElementById('sidebarToggle');
const sidebarOverlay = document.getElementById('sidebarOverlay');
const logoutLink = document.getElementById('tapinLogout');
const logoutModal = document.getElementById('logoutConfirmModal');
const logoutConfirmYes = document.getElementById('logoutConfirmYes');
const logoutConfirmCancel = document.getElementById('logoutConfirmCancel');

function setSidebarState(isOpen) {
    const isMobile = window.innerWidth <= 768;
    document.body.classList.toggle('sidebar-collapsed', !isOpen && !isMobile);

    if (sidebar) {
        sidebar.classList.toggle('is-open', isMobile ? isOpen : true);
    }

    if (sidebarToggle) {
        sidebarToggle.classList.toggle('is-open', isOpen);
        sidebarToggle.setAttribute('aria-expanded', String(isOpen));
    }

    if (sidebarOverlay) {
        sidebarOverlay.classList.toggle('visible', isMobile && isOpen);
    }
}

if (sidebarToggle) {
    sidebarToggle.addEventListener('click', (e) => {
        e.stopPropagation();
        const isMobile = window.innerWidth <= 768;
        if (isMobile) {
            const nextOpenState = !sidebar?.classList.contains('is-open');
            setSidebarState(nextOpenState);
            return;
        }

        const isCollapsed = document.body.classList.contains('sidebar-collapsed');
        // if collapsed -> open (true), otherwise collapse (false)
        setSidebarState(isCollapsed);
    });
}

if (sidebarOverlay) {
    sidebarOverlay.addEventListener('click', () => setSidebarState(false));
}

window.addEventListener('resize', () => {
    if (window.innerWidth > 768) {
        document.body.classList.remove('sidebar-collapsed');
        if (sidebar) sidebar.classList.remove('is-open');
        if (sidebarToggle) {
            sidebarToggle.classList.remove('is-open');
            sidebarToggle.setAttribute('aria-expanded', 'true');
        }
        if (sidebarOverlay) sidebarOverlay.classList.remove('visible');
    } else {
        setSidebarState(false);
    }
});

setSidebarState(true);

// Sidebar nav: toggle active class when clicking items so the blue icon box updates
document.querySelectorAll('.sidebar .nav-item').forEach((navItem) => {
    navItem.addEventListener('click', (e) => {
        // if it's an anchor with a hash, update header, active state, and scroll explicitly
        const href = navItem.getAttribute('href');
        document.querySelectorAll('.sidebar .nav-item').forEach((n) => n.classList.remove('active'));
        navItem.classList.add('active');
        if (href && href.startsWith('#')) {
            e.preventDefault();
            // smooth scroll to section
            scrollToSection(href);
            // update the URL hash without causing a page jump event
            // Use pushState so the click creates a history entry instead of replacing the current one
            try { history.pushState(null, '', href); } catch (err) { location.hash = href; }
        }
    });
});

// Page header mapping for sidebar links (update breadcrumb, title, subtitle)
const pageHeaderMap = {
    '#dashboard': {
        breadcrumb: 'Dashboard',
        title: 'Dashboard Overview',
        subtitle: 'Real-time attendance statistics and system activities.'
    },
    '#reports': {
        breadcrumb: 'Reports',
        title: 'Attendance Reports',
        subtitle: 'Generate, print, and export all attendance and HR reports.'
    },
    '#work-status': {
        breadcrumb: 'Work Status',
        title: 'Work Status',
        subtitle: 'Manage employee work status requests and approvals.'
    },
    '#employees': {
        breadcrumb: 'IPO — Employees Personal Info',
        title: 'Employee Directory',
        subtitle: 'Manage employee personal information and records.'
    },
    '#employee-register': {
        breadcrumb: 'IPO — Employees Personal Info',
        title: 'Employee Register',
        subtitle: 'Register new employees and manage user accounts.'
    },
    '#dtr': {
        breadcrumb: 'IPO — Employees Personal Info',
        title: 'DTR CSC Form',
        subtitle: 'Daily time record forms and employee time logs.'
    },
    '#realtime': {
        breadcrumb: 'Monitoring',
        title: 'Realtime Employee Dashboard',
        subtitle: 'Live RFID scans and activity timeline.'
    },
    '#admin-dashboard': {
        breadcrumb: 'Monitoring',
        title: 'Admin / HR Dashboard',
        subtitle: 'Administrative overview and HR actions.'
    },
    '#introduction': {
        breadcrumb: 'Information',
        title: 'Introduction',
        subtitle: 'Overview and background information.'
    },
    '#iot-attendance': {
        breadcrumb: 'Information',
        title: 'IoT-Based Attendance',
        subtitle: 'Attendance captured via IoT devices.'
    },
    '#settings': {
        breadcrumb: 'Settings',
        title: 'Application Settings',
        subtitle: 'Manage application preferences and integrations.'
    },
    '#profile': {
        breadcrumb: 'Profile',
        title: 'User Profile',
        subtitle: 'View and edit your profile details.'
    }
};

function updatePageHeaderForHash(hash) {
    const info = pageHeaderMap[hash] || pageHeaderMap['#dashboard'];
    // Prefer the global header elements if present
    // Target the global breadcrumb/title/subtitle elements (per-section breadcrumbs removed)
    const bc = document.querySelector('.global-section-actions .breadcrumb') || document.querySelector('.breadcrumb');
    const title = document.querySelector('.global-section-actions .section-title') || document.querySelector('.section-title');
    const subtitle = document.querySelector('.global-section-actions .section-subtitle') || document.querySelector('.section-subtitle');
    if (bc) bc.textContent = info.breadcrumb;
    if (title) title.textContent = info.title;
    if (subtitle) subtitle.textContent = info.subtitle;
}

// Note: header is intentionally kept fixed to Dashboard Overview; do not update it on nav clicks.

// Keep header and active nav in sync on load and when the hash changes
function setActiveNavForHash(hash) {
    const normalized = hash || '#dashboard';
    document.querySelectorAll('.sidebar .nav-item').forEach((n) => {
        try {
            const href = n.getAttribute('href');
            n.classList.toggle('active', href === normalized);
        } catch (e) {}
    });
}

// Initialize header (fixed to dashboard) and active nav on page load
const initialHash = window.location.hash || '#dashboard';
// Keep the visible page header fixed to Dashboard Overview
updatePageHeaderForHash('#dashboard');
setActiveNavForHash(initialHash);

window.addEventListener('hashchange', () => {
    const h = window.location.hash || '#dashboard';
    // keep header fixed; only sync active nav
    setActiveNavForHash(h);
});

// Scroll to the section matching the hash so sections stack vertically
function scrollToSection(hash) {
    const normalized = hash || '#dashboard';
    const id = normalized.replace('#', '');
    const el = document.getElementById(id);
    if (el) {
        const topbar = document.querySelector('.topbar');
        const offset = (topbar ? topbar.offsetHeight : 0) + 8;
        const rect = el.getBoundingClientRect();
        const targetY = window.scrollY + rect.top - offset;
        window.scrollTo({ top: targetY, behavior: 'smooth' });
    } else {
        window.scrollTo({ top: 0, behavior: 'smooth' });
    }
}

// Scroll to initial section on load
scrollToSection(initialHash);
window.addEventListener('hashchange', () => {
    const h = window.location.hash || '#dashboard';
    scrollToSection(h);
});

// Scrollspy: update sidebar active state and page header based on scroll position
(function setupScrollSpy() {
    const sections = Array.from(document.querySelectorAll('.page-section[id]'));
    if (!sections.length) return;

    let current = null;
    // Debounce to avoid rapid flicker when scrolling quickly between sections
    let pendingTimeout = null;
    let pendingHash = null;
    // Slightly increase stability window to reduce rapid flicker when scrolling quickly
    const STABILITY_MS = 200;

    const observer = new IntersectionObserver((entries) => {
        // choose the most visible intersecting section; fall back to nearest-to-top when none intersect
        const visible = entries.filter(e => e.isIntersecting).sort((a,b) => b.intersectionRatio - a.intersectionRatio);
        let id = null;
        if (visible.length > 0 && visible[0].intersectionRatio >= 0.12) {
            id = visible[0].target.id;
        } else {
            // Fallback heuristic: pick the section whose top is closest to the topbar offset
            const topbar = document.querySelector('.topbar');
            const offset = (topbar ? topbar.offsetHeight : 0) + 8;
            let closest = { idx: 0, dist: Infinity };
            sections.forEach((s, idx) => {
                const rectTop = s.getBoundingClientRect().top;
                const dist = Math.abs(rectTop - offset);
                if (dist < closest.dist) closest = { idx, dist };
            });
            if (sections[closest.idx]) id = sections[closest.idx].id;
        }

        if (!id) return;
        const hash = `#${id}`;
        // If already current, nothing to do
        if (current === hash) return;

        // debounce updates: require the same candidate to be present for STABILITY_MS
        pendingHash = hash;
        if (pendingTimeout) clearTimeout(pendingTimeout);
        pendingTimeout = setTimeout(() => {
            // only apply if pendingHash still matches (no newer candidate)
                if (pendingHash === hash) {
                current = hash;
                // keep header fixed; only update active nav state on scroll
                setActiveNavForHash(hash);
            }
            pendingTimeout = null;
        }, STABILITY_MS);
    }, { root: null, rootMargin: '0px 0px -35% 0px', threshold: [0.1, 0.25, 0.5, 0.75] });

    sections.forEach(s => observer.observe(s));
})();

function openLogoutModal() {
    if (logoutModal) logoutModal.classList.remove('hidden');
}

function closeLogoutModal() {
    if (logoutModal) logoutModal.classList.add('hidden');
}

if (logoutLink) {
    logoutLink.addEventListener('click', (event) => {
        event.preventDefault();
        openLogoutModal();
    });
}

if (logoutConfirmCancel) {
    logoutConfirmCancel.addEventListener('click', closeLogoutModal);
}

if (logoutModal) {
    logoutModal.addEventListener('click', (event) => {
        if (event.target === logoutModal) closeLogoutModal();
    });
}

if (logoutConfirmYes) {
    logoutConfirmYes.addEventListener('click', async () => {
        closeLogoutModal();
        try {
            await fetch(`${dashboardApiBaseUrl}/api/logout`, {
                method: 'POST',
                headers: getAuthHeaders(),
                credentials: 'include'
            });
        } finally {
            localStorage.removeItem('tapinToken');
            localStorage.removeItem('tapinUser');
            window.location.replace('../login.html');
        }
    });
}

window.addEventListener('pageshow', verifyDashboardSession);
setInterval(updateClock, 1000);
setInterval(loadDashboardData, 5000);
setInterval(loadActivityFeed, 10000); // Refresh activity feed every 10 seconds

// Poll the lightweight daily-stats endpoint every 3 seconds so the
// Present Today, RFID Scans, and Facial Scans counters stay fresh without
// waiting for the full 5-second dashboard refresh. This endpoint reads
// directly from storage/database/daily_stats.json — the same file the
// RFID taps and face scanner write to.
setInterval(loadDailyStats, 3000);

updateClock();

// Remove the old employee card collapse function since we have a new one
// The new functions handle everything
verifyDashboardSession();

/* ---------------- VERSION AUTO-PULL ---------------- */
const VERSION_URL = 'https://raw.githubusercontent.com/lolenseu/tapin-rfid-attendance-system/refs/heads/main/version.txt';

function loadAppVersion() {
  const versionEl = document.getElementById('versionNumber');
  if (!versionEl) return;
  fetch(VERSION_URL, { cache: 'no-cache' })
    .then((res) => res.ok ? res.text() : null)
    .then((text) => {
      if (!text) return;
      const version = text.trim();
      if (version) versionEl.textContent = version;
    })
    .catch(() => { /* keep fallback version */ });
}

loadAppVersion();
/* ----------------------------------------------- */
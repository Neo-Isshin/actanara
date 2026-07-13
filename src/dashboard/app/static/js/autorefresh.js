  let lastDiaryCount = 0;
  setInterval(async () => {
    try {
      const res = await fetch('/api/diary-list');
      const diaries = await res.json();
      if (diaries.length > lastDiaryCount) {
        lastDiaryCount = diaries.length;
        loadDiaries(); // reload sidebar + pages
      }
    } catch(e) {}
  }, 60000);

  setInterval(async () => {
    try {
      const res = await fetch('/api/report-list');
      const reports = await res.json();
      const reportNav = document.getElementById('report-nav-count');
      if (reportNav) reportNav.textContent = reports.length;
    } catch(e) {}
  }, 120000);

const dormCategories = [...document.querySelectorAll('.dorm-category')];
dormCategories.forEach(button => {
  button.addEventListener('click', () => {
    const open = button.getAttribute('aria-expanded') !== 'true';
    dormCategories.forEach(other => {
      const selected = other === button && open;
      other.setAttribute('aria-expanded', String(selected));
      document.getElementById(other.getAttribute('aria-controls')).hidden = !selected;
    });
    if (open && window.matchMedia('(max-width: 950px)').matches) {
      document.getElementById(button.getAttribute('aria-controls')).scrollIntoView({block:'start',behavior:window.matchMedia('(prefers-reduced-motion: reduce)').matches?'instant':'smooth'});
    }
  });
});

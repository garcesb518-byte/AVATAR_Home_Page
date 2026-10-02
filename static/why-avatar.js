// Hover for a mouse; native button activation supports touch, Enter, and Space.
document.querySelectorAll('.why-value').forEach(card => {
  const reveal = value => card.setAttribute('aria-expanded', String(value));
  card.addEventListener('pointerenter', event => {
    if (event.pointerType === 'mouse') reveal(true);
  });
  card.addEventListener('pointerleave', event => {
    if (event.pointerType === 'mouse') reveal(false);
  });
  card.addEventListener('click', () => reveal(card.getAttribute('aria-expanded') !== 'true'));
  card.addEventListener('blur', () => reveal(false));
  card.addEventListener('keydown', event => {
    if (event.key === 'Escape') reveal(false);
  });
});

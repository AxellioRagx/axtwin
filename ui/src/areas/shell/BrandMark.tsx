/**
 * The X of XTwin, drawn after the X in the Axellio wordmark: two solid
 * chevrons -- one pointing right, one pointing left -- with flat ends,
 * meeting in a short waist at the middle, in the wordmark's lime. Inline
 * SVG so it scales with the brand text and needs no asset.
 */
export function BrandMark() {
  return (
    <svg className="brand-x" viewBox="0 0 84 80" aria-hidden="true" focusable="false">
      <polygon points="0,0 26,0 44,40 26,80 0,80 18,40" />
      <polygon points="84,0 58,0 40,40 58,80 84,80 66,40" />
    </svg>
  );
}

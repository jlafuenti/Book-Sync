import useCoverSrc from '../hooks/useCoverSrc'

/**
 * Drop-in replacement for `<img src={coverSrc(path)} .../>`. coverSrc() is
 * now async (issue #50: short-lived scoped token instead of the long-lived
 * access token in the URL), and a hook can't be called inside a .map()
 * callback -- wrapping it in its own component sidesteps that.
 */
export default function CoverImg({ path, ...imgProps }) {
    const src = useCoverSrc(path)
    return <img src={src} {...imgProps} />
}

package com.booksync.player

/**
 * The port the phone's LAN cast server listens on (issue #931).
 *
 * Fixed and documented so a network that keeps Cast receivers on their own
 * VLAN can allow them to reach the phone with one narrow rule. A random
 * ephemeral port would force that rule to open every port.
 */
const val DEFAULT_CAST_PORT = 8484

/** Unprivileged ports only; below 1024 needs root on Android. */
val CAST_PORT_RANGE: IntRange = 1024..65535

fun isValidCastPort(port: Int): Boolean = port in CAST_PORT_RANGE

/** The port typed into the Account field, or null when it is not a usable one. */
fun parseCastPort(text: String): Int? = text.trim().toIntOrNull()?.takeIf(::isValidCastPort)

/**
 * The read-only line under the port field: the address a firewall rule has to
 * allow. [ip] is the phone's Wi-Fi IPv4, null when there is no Wi-Fi network.
 * No path token: that gates the live session (issue #231).
 */
fun castAddressLine(ip: String?, port: Int): String =
    if (ip == null) "Speakers fetch from: no Wi-Fi connection"
    else "Speakers fetch from http://$ip:$port/"

/** Shown while a session is running on an ephemeral port because [preferred] was taken. */
fun castFallbackLine(actual: Int, preferred: Int): String =
    "Using port $actual because $preferred was busy."

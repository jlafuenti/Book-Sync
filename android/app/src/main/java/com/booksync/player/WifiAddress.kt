package com.booksync.player

import android.content.Context
import android.net.ConnectivityManager
import android.net.NetworkCapabilities
import java.net.Inet4Address

/**
 * The phone's Wi-Fi IPv4, shared by the service (where the LAN cast server
 * binds its address for the receiver) and the Account screen (which shows the
 * address a firewall rule has to allow, issue #931). One walk, so the screen
 * can never show an address the service would not use.
 */
object WifiAddress {

    /**
     * Finds the phone's Wi-Fi IPv4 by walking each network's LinkProperties.
     * Returns null if there is no Wi-Fi network (e.g. cellular-only).
     */
    fun detectIpv4(context: Context): String? {
        val cm = context.getSystemService(Context.CONNECTIVITY_SERVICE) as? ConnectivityManager
            ?: return null
        // Walk every network -- cast typically requires Wi-Fi, but a multi-homed phone on
        // Wi-Fi + cellular might have a non-default Wi-Fi network we still want to use.
        for (network in cm.allNetworks) {
            val caps = cm.getNetworkCapabilities(network) ?: continue
            if (!caps.hasTransport(NetworkCapabilities.TRANSPORT_WIFI)) continue
            val props = cm.getLinkProperties(network) ?: continue
            for (linkAddress in props.linkAddresses) {
                val addr = linkAddress.address
                if (addr is Inet4Address && !addr.isLoopbackAddress && !addr.isAnyLocalAddress) {
                    return addr.hostAddress
                }
            }
        }
        return null
    }
}

package com.booksync.ui.ebooks

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.booksync.data.local.entity.EBookEntity
import com.booksync.data.repository.BookSyncRepository
import dagger.hilt.android.lifecycle.HiltViewModel
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch
import java.io.File
import javax.inject.Inject

@HiltViewModel
class EbooksViewModel @Inject constructor(
    private val repository: BookSyncRepository,
) : ViewModel() {
    val ebooks = repository.getEbooksFlow()

    private val _refreshing = MutableStateFlow(false)
    val refreshing = _refreshing.asStateFlow()

    private val _downloading = MutableStateFlow<Set<Int>>(emptySet())
    val downloading = _downloading.asStateFlow()

    init {
        refresh()
    }

    fun refresh() {
        viewModelScope.launch {
            _refreshing.value = true
            try {
                repository.refreshEbooks()
            } catch (_: Exception) {}
            _refreshing.value = false
        }
    }

    fun downloadEbook(ebook: EBookEntity) {
        viewModelScope.launch {
            _downloading.value = _downloading.value + ebook.id
            try {
                if (!ebook.isDownloaded) repository.downloadStandaloneEbook(ebook)
            } catch (_: Exception) {}
            _downloading.value = _downloading.value - ebook.id
        }
    }
}

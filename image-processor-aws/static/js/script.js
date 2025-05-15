document.addEventListener('DOMContentLoaded', function() {
    const uploadForm = document.getElementById('uploadForm');
    const imageFile = document.getElementById('imageFile');
    const uploadStatus = document.getElementById('uploadStatus');
    const imageGallery = document.getElementById('imageGallery');
    const refreshImagesButton = document.getElementById('refreshImages');
    const galleryMessage = document.getElementById('galleryMessage');

    if (uploadForm) {
        uploadForm.addEventListener('submit', async function(event) {
            event.preventDefault();
            if (!imageFile.files || imageFile.files.length === 0) {
                uploadStatus.textContent = 'Please select a file to upload.';
                uploadStatus.style.color = 'red';
                return;
            }

            uploadStatus.textContent = 'Uploading...';
            uploadStatus.style.color = 'blue';

            const formData = new FormData();
            formData.append('imageFile', imageFile.files[0]);

            try {
                const response = await fetch('/upload', {
                    method: 'POST',
                    body: formData
                });
               
                let result;
                const contentType = response.headers.get("content-type");
                if (contentType && contentType.indexOf("application/json") !== -1) {
                    result = await response.json();
                } else {
                    // Handle non-JSON response
                    const errorText = await response.text();
                    console.error("Non-JSON response from /upload:", errorText);
                    throw new Error(`Server returned non-JSON response. Status: ${response.status}. Check server logs.`);
                }


                if (response.ok) {
                    uploadStatus.textContent = result.message || 'Upload successful! Processing...';
                    uploadStatus.style.color = 'green';
                    imageFile.value = ''; // Clear the file input
                    fetchImages();
                } else {
                    uploadStatus.textContent = `Error: ${result.error || 'Upload failed. Check server logs.'}`;
                    uploadStatus.style.color = 'red';
                }
            } catch (error) {
                console.error('Upload error:', error);
                uploadStatus.textContent = `Upload failed: ${error.message}. See console for details.`;
                uploadStatus.style.color = 'red';
            }
        });
    }

    async function fetchImages() {
        console.log('Attempting to fetch images...');
        if (galleryMessage) galleryMessage.textContent = 'Loading images...';
        try {        
            const response = await fetch('/api/images');
            console.log('Response from /api/images:', response.status, response.ok); 
            if (!response.ok) {
                let errorMsg = `HTTP error! status: ${response.status}`;
                try {
                    const errorResult = await response.json();
                    errorMsg = errorResult.error || errorMsg;
                } catch (e) { /* Ignore if response is not JSON */ }
                throw new Error(errorMsg);
            }
            const images = await response.json();
            console.log('Images data received:', images);
            renderGallery(images);
        } catch (error) {
            console.error('Error fetching images:', error);
            if (imageGallery) imageGallery.innerHTML = `<p style="color:red;">Error loading images: ${error.message}</p>`;
            if (galleryMessage) galleryMessage.textContent = 'Could not load images.';
        }
    }

    function renderGallery(images) {
        if (!imageGallery) {
            console.error("imageGallery element not found");
            return;
        }
        imageGallery.innerHTML = ''; // Clear existing images

        if (!images || images.length === 0) {
            if (galleryMessage) {
                galleryMessage.textContent = 'No images found. Upload an image!';
                galleryMessage.style.display = 'block';
            }
            return;
        }

        if (galleryMessage) galleryMessage.style.display = 'none';


        images.sort((a, b) => {
            const dateA = new Date(a.UploadTimestamp || 0).getTime();
            const dateB = new Date(b.UploadTimestamp || 0).getTime();
            return dateB - dateA; // Newest first
        });

        images.forEach(image => {
            const itemDiv = document.createElement('div');
            itemDiv.classList.add('gallery-item');

            let effectiveThumbnailUrl = image.displayUrl;
            if (image.ResizedUrls && typeof image.ResizedUrls === 'object' && image.ResizedUrls.thumbnail) {
                effectiveThumbnailUrl = image.ResizedUrls.thumbnail;
            }

            const thumbnailHtml = effectiveThumbnailUrl
                ? `<img src="${effectiveThumbnailUrl}" alt="${image.OriginalFilename || 'Uploaded image'}" class="thumbnail">`
                : `<p>No preview</p>`;

            const status = (image.ProcessingStatus || 'UNKNOWN').toUpperCase();
            const statusClassSuffix = status.replace(/[^A-Z0-9_-]/gi, '');

            let originalLinkHtml = '';
            if (image.displayUrl) {
                 originalLinkHtml = `<a href="${image.displayUrl}" target="_blank" title="View Original Image">Original</a>`;
            } else if (image.OriginalS3Url) { // Fallback to S3 URI if no displayUrl
                 originalLinkHtml = `<a href="#" title="Original S3 URI: ${image.OriginalS3Url}" onclick="alert('S3 URI: ${image.OriginalS3Url}'); return false;">Original URI</a>`;
            }


            let mediumLinkHtml = '';
            if (image.ResizedUrls && typeof image.ResizedUrls === 'object' && image.ResizedUrls.medium) {
                mediumLinkHtml = `<a href="${image.ResizedUrls.medium}" target="_blank">Medium</a>`;
            }

            let largeLinkHtml = '';
            if (image.ResizedUrls && typeof image.ResizedUrls === 'object' && image.ResizedUrls.large) {
                largeLinkHtml = `<a href="${image.ResizedUrls.large}" target="_blank">Large</a>`;
            }

            itemDiv.innerHTML = `
                ${thumbnailHtml}
                <p class="filename"><strong>${image.OriginalFilename || 'N/A'}</strong></p>
                <p class="status status-${statusClassSuffix}">${status || 'UNKNOWN'}</p>
                <p class="image-id">ID: ${image.ImageID ? image.ImageID.substring(0, 8) + '...' : 'N/A'}</p>
                <div class="resized-links">
                    ${originalLinkHtml}
                    ${mediumLinkHtml}
                    ${largeLinkHtml}
                </div>
            `;
            imageGallery.appendChild(itemDiv);
        });
    }

    if (refreshImagesButton) {
        refreshImagesButton.addEventListener('click', fetchImages);
    }

    fetchImages();
});
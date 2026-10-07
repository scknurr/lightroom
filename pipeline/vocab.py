"""Zero-shot tag vocabulary. Each category is softmaxed independently; tags win only within their category."""

CATEGORIES = {
    'scene': [
        'beach', 'ocean waves', 'lake', 'river', 'waterfall', 'mountains', 'forest', 'desert', 'snowy landscape',
        'farmland and fields', 'prairie', 'city street', 'city skyline', 'small town', 'suburban neighborhood',
        'park', 'garden', 'backyard', 'home interior', 'kitchen', 'restaurant or bar', 'office', 'studio backdrop',
        'concert stage', 'stadium or sports field', 'gym', 'church', 'museum or gallery', 'airport', 'highway or road',
        'bridge', 'industrial site', 'construction site', 'parking lot', 'campsite', 'boat on water', 'night sky',
        'sky and clouds', 'storm clouds', 'underwater', 'aerial view', 'cave', 'canyon', 'island', 'harbor or marina',
    ],
    'subject': [
        'portrait of one person', 'couple', 'group of people', 'crowd', 'baby', 'child', 'family', 'dog', 'cat',
        'horse', 'bird', 'wildlife', 'insect', 'flowers', 'tree', 'food', 'drink or cocktail', 'car', 'motorcycle',
        'bicycle', 'truck', 'airplane', 'train', 'boat', 'architecture', 'building facade', 'interior design',
        'musician performing', 'athlete in action', 'dancer', 'fashion model', 'product shot', 'technology or gadgets',
        'artwork', 'sign or text', 'fireworks', 'sunset', 'sunrise', 'moon', 'stars', 'lightning', 'fire or campfire',
        'water reflection', 'shadows and patterns', 'texture close-up', 'drone', 'video camera gear', 'computer screen',
    ],
    'light': [
        'golden hour light', 'blue hour', 'harsh midday sun', 'overcast soft light', 'backlit', 'silhouette',
        'night with city lights', 'neon light', 'studio lighting', 'candlelight', 'foggy or misty', 'rainy',
        'dramatic light rays', 'flash photography', 'low key dark', 'high key bright',
    ],
    'style': [
        'black and white photo', 'minimalist composition', 'symmetrical composition', 'leading lines',
        'shallow depth of field bokeh', 'macro photography', 'long exposure motion blur', 'wide angle landscape',
        'telephoto compression', 'street photography', 'documentary candid moment', 'fine art photography',
        'editorial fashion photography', 'abstract photography', 'aerial drone photography', 'vintage film look',
        'action freeze motion', 'panning motion', 'cinematic still', 'travel photography', 'snapshot',
    ],
    'event': [
        'wedding', 'birthday party', 'holiday celebration', 'concert or live music', 'sporting event',
        'hiking trip', 'beach vacation', 'road trip', 'corporate event or conference', 'video production shoot',
        'graduation', 'festival', 'christmas', 'halloween', 'everyday life', 'no particular event',
    ],
    'junk': [
        'a good photograph', 'a screenshot', 'a photo of a document or receipt', 'a photo of a whiteboard or slide',
        'an accidental photo of the ground', 'a blurry out of focus photo', 'a completely dark photo',
        'a photo of a computer monitor', 'a meme or graphic', 'a test shot of a gray card or color chart',
    ],
}

PROMPT = 'a photo of {}'

"""Native MiniMax H3 graph for the pinned DSW ComfyUI."""
UNET = 'minimax_h3_ref2va_pruned_int8_convrot.safetensors'
CLIP = 'qwen3vl_32b_minimax_h3_int8_convrot.safetensors'
VIDEO_VAE = 'minimax_h3_video_vae_fp16.safetensors'
AUDIO_VAE = 'minimax_h3_audio_vae_fp32.safetensors'
TURBO = 'minimax_h3_ref2v_turbo_4step_v0.1_comfyui_bf16.safetensors'
OUTPUT = 'save'


def profile(reference_count, *, motion=False, preview=False, voice_count=0):
    if not 1 <= reference_count <= 5:
        raise ValueError('H3 每镜需要 1 至 5 张参考图。')
    if type(voice_count) is not int or not 0 <= voice_count <= 3:
        raise ValueError('H3 每镜最多三段已确认的声音参考。')
    graph = {}

    def node(name, class_type, **inputs):
        graph[name] = {'class_type': class_type, 'inputs': inputs}
        return [name, 0]

    # ComfyUI accepts numeric or string node identifiers. These labels also aid
    # investigation of node errors returned by /prompt and /history.
    model = node('model', 'UNETLoader', unet_name=UNET, weight_dtype='default')
    clip = node('text', 'CLIPLoader', clip_name=CLIP, type='minimax', device='default')
    video_vae = node('video_vae', 'VAELoader', vae_name=VIDEO_VAE)
    audio_vae = node('audio_vae', 'VAELoader', vae_name=AUDIO_VAE)
    if preview:
        model = node('turbo', 'LoraLoaderModelOnly', model=model, lora_name=TURBO, strength_model=1.0)

    references = []
    conditioning_refs = {}
    for index in range(reference_count):
        name = f'image_{index}'
        conditioning_refs[f'ref_images.ref_image_{index}'] = node(name, 'LoadImage', image='')
        references.append((name, 'image'))
    voice_nodes = []
    for index in range(voice_count):
        name = f'voice_{index}'
        conditioning_refs[f'ref_audios.ref_audio_{index}'] = node(name, 'LoadAudio', audio='')
        voice_nodes.append(name)
    if motion:
        motion_video = node('motion', 'LoadVideo', file='')
        frames = node('motion_frames', 'GetVideoComponents', video=motion_video)
        conditioning_refs['ref_videos.ref_video_0'] = frames

    conditioning = node('conditioning', 'MiniMaxH3ReferenceToVideo', clip=clip, vae=video_vae,
                        audio_vae=audio_vae, prompt='', width=1344, height=768, length=124,
                        ref_image_size='match', **conditioning_refs)
    noise = node('noise', 'RandomNoise', noise_seed=0)
    sampler = node('sampler', 'KSamplerSelect', sampler_name='res_multistep')
    sigmas = node('sigmas', 'BasicScheduler', model=model, scheduler='simple',
                  steps=4 if preview else 20, denoise=1.0)
    guider = node('guider', 'BasicGuider', model=model, conditioning=conditioning)
    sampled = node('sample', 'SamplerCustomAdvanced', noise=noise, guider=guider,
                   sampler=sampler, sigmas=sigmas, latent_image=['conditioning', 1])
    images = node('frames', 'VAEDecode', samples=sampled, vae=video_vae)
    audio = node('audio', 'VAEDecodeAudio', samples=sampled, vae=audio_vae)
    video = node('video', 'CreateVideo', images=images, audio=audio, fps=24.0)
    node(OUTPUT, 'SaveVideo', video=video, filename_prefix='morpho-h3/take',
         format='mp4', **{'format.codec': 'h264'})
    return {'prompt': graph, 'references': references, 'voices': voice_nodes, 'output': OUTPUT,
            'fields': {'text': ('conditioning', 'prompt'), 'width': ('conditioning', 'width'),
                       'height': ('conditioning', 'height'), 'frames': ('conditioning', 'length')}}

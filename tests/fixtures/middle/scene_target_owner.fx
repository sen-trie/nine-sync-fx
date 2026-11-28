#ifndef SIDE
#define SIDE 0
#endif
texture HitRT : OFFSCREENRENDERTARGET <
 int Width=256; int Height=256; string Format="A8R8G8B8";
 float4 ClearColor={.07,.12,.35,1}; float ClearDepth=1;
 bool AntiAlias=false;
 string DefaultEffect="self=hide;Marisa-target.pmx=scene_target_draw.fx;*=hide;";
>;
sampler View = sampler_state { Texture=<HitRT>; MinFilter=POINT; MagFilter=POINT; MipFilter=NONE; AddressU=CLAMP; AddressV=CLAMP; };
struct Output { float4 p:POSITION; float2 uv:TEXCOORD0; };
float2 ViewportSize : VIEWPORTPIXELSIZE;
Output VS(float4 p:POSITION,float2 uv:TEXCOORD0) { Output o; o.p=p; o.uv=uv+.5/ViewportSize; return o; }
float4 PS(Output i):COLOR {
#if SIDE == 0
 clip(.5-i.uv.x);
 return tex2D(View,float2(i.uv.x*2,i.uv.y));
#else
 clip(i.uv.x-.5);
 return tex2D(View,float2((i.uv.x-.5)*2,i.uv.y));
#endif
}
technique Main < string MMDPass="object"; > {
 pass Display < string Script="Draw=Buffer;"; > {
  ZEnable=false; ZWriteEnable=false; AlphaBlendEnable=false;
  VertexShader=compile vs_3_0 VS(); PixelShader=compile ps_3_0 PS();
 }
}

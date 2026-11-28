// Independent tilted near-plane and intersecting UV-grid raster diagnostic.
#ifndef CLIP_MODE
#define CLIP_MODE 0
#endif
#ifndef ORTHOGRAPHIC
#define ORTHOGRAPHIC 0
#endif
float4x4 WVP:WORLDVIEWPROJECTION;
float4x4 WV:WORLDVIEW;
float4 D:DIFFUSE <string Object="Geometry";>;
struct O {float4 p:POSITION;float2 uv:TEXCOORD0;};
O VS(float4 p:POSITION,float2 uv:TEXCOORD0) {
 O o;float4 view=mul(p,WV);o.p=mul(p,WVP);
 // Fix the diagnostic near/far at 1 and 16 for both projection modes.
#if ORTHOGRAPHIC
 o.p.z=(view.z-1)/15;
#else
 o.p.z=(view.z*16-16)/15;
#endif
 if(D.b<.5) {
#if CLIP_MODE == 1
  o.p.z-=o.p.w*uv.y;
#elif CLIP_MODE == 2
  o.p.w*=1.25+.75*uv.x;
#endif
 }
 o.uv=uv;return o;
}
float4 PS(O i):COLOR {
 float gridShade=(frac(i.uv.x*8)<.1 || frac(i.uv.y*8)<.1)? .35:1;
 return float4(float3(i.uv.x,.2+.6*i.uv.y,.2+.7*D.b)*gridShade,1);
}
technique Main <string MMDPass="object";> {
 pass Draw {
  ZEnable=true;ZWriteEnable=true;ZFunc=LESSEQUAL;
  CullMode=NONE;AlphaBlendEnable=false;
  VertexShader=compile vs_3_0 VS();PixelShader=compile ps_3_0 PS();
 }
}
